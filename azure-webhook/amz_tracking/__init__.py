"""
Amazon → Linnworks tracking webhook (Azure Functions v1 model)
──────────────────────────────────────────────────────────────
Receives Amazon SNS ORDER_CHANGE notifications, fetches the
tracking number from Amazon SP-API, and writes it to Linnworks.
"""

import json, logging, os, time
import azure.functions as func
import requests

logger = logging.getLogger("amz_lw_sync")

# ── Credentials from Azure Function App Settings ──────────────
AMZ_APP_ID        = os.environ["ASL_LWA_APP_ID"]
AMZ_CLIENT_SECRET = os.environ["ASL_LWA_CLIENT_SECRET"]
AMZ_REFRESH_TOKEN = os.environ["ASL_LWA_REFRESH_TOKEN"]

LW_APP_ID     = os.environ.get("LINNWORKS_APP_ID")     or os.environ.get("LINNWORKS_CLIENT_ID")
LW_APP_SECRET = os.environ.get("LINNWORKS_APP_SECRET") or os.environ.get("LINNWORKS_CLIENT_SECRET")
LW_TOKEN      = os.environ["LINNWORKS_TOKEN"]

AMZ_ORDERS_URL = "https://sellingpartnerapi-eu.amazon.com/orders/2026-01-01/orders"
MARKETPLACE_ID = "A1F83G8C2ARO7P"

FULFILMENT_CENTRES = [
    "00000000-0000-0000-0000-000000000000",
    "64e6b463-fbaa-490f-b3ec-2e53eb197e2d",
    "8e8ac5eb-ebf7-485a-9545-0766dbf76ada",
    "db86ec44-ff4f-4d25-82c1-ff0a488c005d",
    "9799d0f6-684c-4b61-93fc-8244ae0ced58",
    "733af7e9-3f17-465f-8ca1-cc5951468453",   # TFC — current orders live here
    "d005ecc9-1dcf-4ccc-80c0-8844d0dcab95",
]


def main(req: func.HttpRequest) -> func.HttpResponse:
    msg_type = req.headers.get("x-amz-sns-message-type", "")
    logger.info(f"Received request | SNS type: '{msg_type}' | method: {req.method}")

    # ── Health check (GET) ────────────────────────────────────
    if req.method == "GET" or not msg_type:
        return _json_response({"status": "ok",
                               "message": "Amazon → Linnworks tracking webhook"})

    try:
        body = req.get_json()
    except Exception:
        return _json_response({"error": "invalid JSON"}, 400)

    # ── SNS subscription confirmation (one-time) ─────────────
    if msg_type == "SubscriptionConfirmation":
        url = body.get("SubscribeURL")
        if url:
            requests.get(url, timeout=30)
            logger.info("SNS subscription confirmed")
            return _json_response({"status": "confirmed"})
        return _json_response({"error": "no SubscribeURL"}, 400)

    # ── ORDER_CHANGE notification ─────────────────────────────
    if msg_type == "Notification":
        try:
            payload = json.loads(body.get("Message", "{}"))
        except Exception:
            return _json_response({"error": "bad Message JSON"}, 400)

        order_id = _extract_order_id(payload)
        if not order_id:
            logger.warning(f"No order ID in payload: {str(payload)[:200]}")
            return _json_response({"status": "skipped", "reason": "no order ID"})

        logger.info(f"Processing: {order_id}")
        result = _sync(order_id)
        logger.info(f"Result for {order_id}: {result}")
        return _json_response(result)

    # Unknown type — return 200 so SNS doesn't retry
    return _json_response({"status": "ignored", "type": msg_type})


# ════════════════════════════════════════════════════════════════
# SYNC LOGIC
# ════════════════════════════════════════════════════════════════

def _sync(order_id):
    amz_token = _auth_amazon()
    if not amz_token:
        return {"status": "error", "reason": "Amazon auth failed"}

    tracking = _get_tracking(amz_token, order_id)
    if not tracking:
        return {"status": "skipped", "reason": "no tracking on Amazon yet"}

    lw_token, lw_server = _auth_linnworks()
    if not lw_token:
        return {"status": "error", "reason": "Linnworks auth failed"}

    lw_order = _find_lw_order(lw_token, lw_server, order_id)
    if not lw_order:
        return {"status": "skipped", "reason": f"{order_id} not in Linnworks"}

    if lw_order["existing_tracking"] == tracking["tracking_number"]:
        return {"status": "skipped", "reason": "tracking already up to date"}

    ok = _write_tracking(lw_token, lw_server,
                         lw_order["lw_guid"],
                         tracking["tracking_number"],
                         tracking["carrier"],
                         lw_order["shipping_info"])

    if ok:
        return {"status": "updated", "order_id": order_id,
                "tracking": tracking["tracking_number"],
                "carrier":  tracking["carrier"]}

    return {"status": "error", "reason": "SetOrderShippingInfo failed"}


# ════════════════════════════════════════════════════════════════
# AMAZON HELPERS
# ════════════════════════════════════════════════════════════════

def _extract_order_id(payload):
    for path in [
        lambda p: p.get("OrderChangeNotification", {}).get("AmazonOrderId"),
        lambda p: p.get("AmazonOrderId"),
        lambda p: p.get("Summary", {}).get("AmazonOrderId"),
    ]:
        val = path(payload)
        if val:
            return val.strip()
    return None


def _auth_amazon():
    try:
        r = requests.post(
            "https://api.amazon.com/auth/o2/token",
            data={"grant_type": "refresh_token",
                  "refresh_token": AMZ_REFRESH_TOKEN,
                  "client_id": AMZ_APP_ID,
                  "client_secret": AMZ_CLIENT_SECRET},
            timeout=30,
        )
        return r.json().get("access_token")
    except Exception as e:
        logger.error(f"Amazon auth error: {e}")
        return None


def _get_tracking(amz_token, order_id):
    def _fetch():
        return requests.get(
            f"{AMZ_ORDERS_URL}/{order_id}",
            headers={"x-amz-access-token": amz_token},
            params={"includedData": "PACKAGES"},
            timeout=30,
        )

    r = _fetch()
    if r.status_code == 429:
        time.sleep(30)
        r = _fetch()
    if r.status_code != 200:
        return None

    try:
        order = r.json().get("order", r.json())
    except Exception:
        return None

    for pkg in order.get("packages", []):
        tn = (pkg.get("trackingNumber") or "").strip()
        if tn:
            return {"tracking_number": tn,
                    "carrier": (pkg.get("carrier") or "Amazon Shipping").strip()}

    for item in order.get("orderItems", []):
        for pkg in item.get("packages", []):
            tn = (pkg.get("trackingNumber") or "").strip()
            if tn:
                return {"tracking_number": tn,
                        "carrier": (pkg.get("carrier") or "Amazon Shipping").strip()}
    return None


# ════════════════════════════════════════════════════════════════
# LINNWORKS HELPERS
# ════════════════════════════════════════════════════════════════

def _auth_linnworks():
    try:
        r = requests.post(
            "https://api.linnworks.net/api/Auth/AuthorizeByApplication",
            json={"ApplicationId": LW_APP_ID,
                  "ApplicationSecret": LW_APP_SECRET,
                  "Token": LW_TOKEN},
            timeout=30,
        )
        if r.status_code == 200:
            d = r.json()
            return d["Token"], d["Server"]
    except Exception as e:
        logger.error(f"Linnworks auth error: {e}")
    return None, None


def _find_lw_order(lw_token, server, amazon_order_id):
    for fc_id in FULFILMENT_CENTRES:
        try:
            r = requests.post(
                f"{server}/api/Orders/GetOpenOrders",
                headers={"Authorization": lw_token},
                data={
                    "entriesPerPage":   50,
                    "pageNumber":       1,
                    "filters":          "",
                    "sorting":          json.dumps([{"Direction": 0, "FieldCode": "GENERAL_INFO_DATE"}]),
                    "fulfilmentCenter": fc_id,
                    "additionalFilter": amazon_order_id,
                },
                timeout=30,
            )
            if r.status_code != 200:
                continue
            for order in r.json().get("Data", []):
                gi  = order.get("GeneralInfo", {})
                if (gi.get("ReferenceNum") or "").strip() == amazon_order_id:
                    si = order.get("ShippingInfo", {})
                    return {
                        "lw_guid":           order.get("OrderId", ""),
                        "shipping_info":     si,
                        "existing_tracking": (si.get("TrackingNumber") or "").strip(),
                    }
        except Exception as e:
            logger.error(f"LW search error fc={fc_id}: {e}")
    return None


def _write_tracking(lw_token, server, lw_guid, tracking_number, carrier, existing_si):
    info = {
        "Vendor":            existing_si.get("Vendor")            or carrier,
        "PostalServiceId":   existing_si.get("PostalServiceId")   or "00000000-0000-0000-0000-000000000000",
        "PostalServiceName": existing_si.get("PostalServiceName") or carrier,
        "TotalWeight":       existing_si.get("TotalWeight",    0),
        "ItemWeight":        existing_si.get("ItemWeight",     0),
        "PackageCategoryId": existing_si.get("PackageCategoryId") or "00000000-0000-0000-0000-000000000000",
        "PackageCategory":   existing_si.get("PackageCategory",   ""),
        "PackageTypeId":     existing_si.get("PackageTypeId")     or "00000000-0000-0000-0000-000000000000",
        "PackageType":       existing_si.get("PackageType",       ""),
        "PostageCost":       existing_si.get("PostageCost",    0),
        "PostageCostExTax":  existing_si.get("PostageCostExTax", 0),
        "TrackingNumber":    tracking_number,
        "ManualAdjust":      False,
    }
    try:
        r = requests.post(
            f"{server}/api/Orders/SetOrderShippingInfo",
            headers={"Authorization": lw_token, "Content-Type": "application/json"},
            json={"orderId": lw_guid, "info": info},
            timeout=30,
        )
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Write tracking error: {e}")
        return False


def _json_response(data, status=200):
    return func.HttpResponse(
        json.dumps(data),
        status_code=status,
        mimetype="application/json",
    )
