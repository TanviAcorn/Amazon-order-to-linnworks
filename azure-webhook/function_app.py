"""
function_app.py — Azure Function: Amazon SNS webhook → Linnworks tracking sync
───────────────────────────────────────────────────────────────────────────────
Amazon fires an ORDER_CHANGE notification via SNS when an order is shipped
(i.e. a tracking number is assigned). This function:

  1. Receives the SNS HTTP POST
  2. Handles SNS subscription confirmation (one-time, automatic)
  3. Extracts the Amazon Order ID from the notification
  4. Fetches the tracking number from Amazon SP-API
  5. Finds the order in Linnworks (across all fulfilment centres)
  6. Writes the tracking number to Linnworks

Deployed as an Azure Function (Consumption plan) — ~free for this volume.
"""

import azure.functions as func
import json, logging, os, requests, time

# ── Logging ───────────────────────────────────────────────────
logger = logging.getLogger("amz_lw_sync")
logger.setLevel(logging.INFO)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


# ════════════════════════════════════════════════════════════════
# CREDENTIALS  (set these in Azure Function App → Configuration)
# ════════════════════════════════════════════════════════════════

AMZ_APP_ID        = os.environ["ASL_LWA_APP_ID"]
AMZ_CLIENT_SECRET = os.environ["ASL_LWA_CLIENT_SECRET"]
AMZ_REFRESH_TOKEN = os.environ["ASL_LWA_REFRESH_TOKEN"]

LW_APP_ID         = os.environ.get("LINNWORKS_APP_ID")     or os.environ.get("LINNWORKS_CLIENT_ID")
LW_APP_SECRET     = os.environ.get("LINNWORKS_APP_SECRET") or os.environ.get("LINNWORKS_CLIENT_SECRET")
LW_TOKEN          = os.environ["LINNWORKS_TOKEN"]

AMZ_ORDERS_URL    = "https://sellingpartnerapi-eu.amazon.com/orders/2026-01-01/orders"
MARKETPLACE_ID    = "A1F83G8C2ARO7P"

# All Linnworks fulfilment centres — orders can be in any of these
FULFILMENT_CENTRES = [
    "00000000-0000-0000-0000-000000000000",   # Default
    "64e6b463-fbaa-490f-b3ec-2e53eb197e2d",   # ASL Amazon FBA
    "8e8ac5eb-ebf7-485a-9545-0766dbf76ada",   # ASL-TFC
    "db86ec44-ff4f-4d25-82c1-ff0a488c005d",   # Jambo Amazon FBA
    "9799d0f6-684c-4b61-93fc-8244ae0ced58",   # Non-WMS managed
    "733af7e9-3f17-465f-8ca1-cc5951468453",   # TFC
    "d005ecc9-1dcf-4ccc-80c0-8844d0dcab95",   # TFC-FBA
]


# ════════════════════════════════════════════════════════════════
# WEBHOOK ENTRY POINT
# URL will be: https://<app>.azurewebsites.net/api/amz-tracking
# ════════════════════════════════════════════════════════════════

@app.route(route="amz-tracking", methods=["GET", "POST"])
def amz_tracking_webhook(req: func.HttpRequest) -> func.HttpResponse:
    """
    Receives Amazon SNS notifications for ORDER_CHANGE events.

    SNS sends two types of requests:
      1. SubscriptionConfirmation — one-time when you first subscribe
         We auto-confirm by hitting the SubscribeURL Amazon provides.
      2. Notification — the actual order change event
         We extract the order ID, fetch tracking, push to Linnworks.
    """
    # ── Parse body ────────────────────────────────────────────
    try:
        body = req.get_json()
    except Exception:
        body = {}

    msg_type = req.headers.get("x-amz-sns-message-type", "")
    logger.info(f"SNS message type: {msg_type}")

    # ── 1. SNS subscription confirmation (one-time) ───────────
    if msg_type == "SubscriptionConfirmation":
        subscribe_url = body.get("SubscribeURL")
        if subscribe_url:
            r = requests.get(subscribe_url, timeout=30)
            logger.info(f"SNS subscription confirmed: {r.status_code}")
            return func.HttpResponse("Subscription confirmed", status_code=200)
        return func.HttpResponse("Missing SubscribeURL", status_code=400)

    # ── 2. Actual notification ────────────────────────────────
    if msg_type == "Notification":
        try:
            payload = json.loads(body.get("Message", "{}"))
        except Exception:
            logger.error("Failed to parse SNS Message JSON")
            return func.HttpResponse("Bad message", status_code=400)

        order_id = _extract_order_id(payload)
        if not order_id:
            logger.warning(f"No order ID found in notification: {json.dumps(payload)[:300]}")
            return func.HttpResponse("No order ID", status_code=200)

        logger.info(f"Processing order: {order_id}")

        result = sync_order_tracking(order_id)

        if result["status"] == "updated":
            logger.info(f"✅ {order_id} → tracking {result['tracking']} written to Linnworks")
        elif result["status"] == "skipped":
            logger.info(f"⏭️  {order_id} — {result['reason']}")
        else:
            logger.error(f"❌ {order_id} — {result['reason']}")

        # Always return 200 to SNS — otherwise it retries aggressively
        return func.HttpResponse(json.dumps(result), status_code=200,
                                 mimetype="application/json")

    # ── 3. Health check / unknown ─────────────────────────────
    return func.HttpResponse(
        json.dumps({"status": "ok", "message": "Amazon → Linnworks tracking webhook"}),
        status_code=200, mimetype="application/json"
    )


# ════════════════════════════════════════════════════════════════
# CORE SYNC LOGIC
# ════════════════════════════════════════════════════════════════

def sync_order_tracking(order_id: str) -> dict:
    """
    Full sync for one order:
      1. Get Amazon access token
      2. Fetch tracking from Amazon
      3. Authenticate with Linnworks
      4. Find order in Linnworks
      5. Write tracking

    Returns a result dict with status/reason for logging.
    """
    # Step 1 — Amazon auth
    amz_token = _auth_amazon()
    if not amz_token:
        return {"status": "error", "reason": "Amazon auth failed"}

    # Step 2 — Get tracking from Amazon
    tracking = _get_tracking(amz_token, order_id)
    if not tracking:
        return {"status": "skipped", "reason": "No tracking on Amazon yet"}

    # Step 3 — Linnworks auth
    lw_token, lw_server = _auth_linnworks()
    if not lw_token:
        return {"status": "error", "reason": "Linnworks auth failed"}

    # Step 4 — Find order in Linnworks
    lw_order = _find_lw_order(lw_token, lw_server, order_id)
    if not lw_order:
        return {"status": "skipped", "reason": f"{order_id} not found in Linnworks"}

    # Skip if tracking already set to the same value
    if lw_order["existing_tracking"] == tracking["tracking_number"]:
        return {"status": "skipped", "reason": "Tracking already up to date"}

    # Step 5 — Write tracking
    ok = _write_tracking(lw_token, lw_server,
                         lw_order["lw_guid"],
                         tracking["tracking_number"],
                         tracking["carrier"],
                         lw_order["shipping_info"])

    if ok:
        return {
            "status":   "updated",
            "order_id": order_id,
            "tracking": tracking["tracking_number"],
            "carrier":  tracking["carrier"],
            "lw_guid":  lw_order["lw_guid"],
        }

    return {"status": "error", "reason": "Linnworks SetOrderShippingInfo failed"}


# ════════════════════════════════════════════════════════════════
# HELPERS — Amazon
# ════════════════════════════════════════════════════════════════

def _extract_order_id(payload: dict) -> str | None:
    """
    Pull the Amazon Order ID out of an ORDER_CHANGE notification payload.
    Amazon nests it differently depending on the notification version.
    """
    # v2 notification shape
    order_id = (payload.get("OrderChangeNotification", {})
                       .get("AmazonOrderId", ""))
    if order_id:
        return order_id.strip()

    # Flat shape
    order_id = payload.get("AmazonOrderId", "")
    if order_id:
        return order_id.strip()

    # Summary shape
    order_id = (payload.get("Summary", {})
                       .get("AmazonOrderId", ""))
    if order_id:
        return order_id.strip()

    return None


def _auth_amazon() -> str | None:
    try:
        r = requests.post(
            "https://api.amazon.com/auth/o2/token",
            data={
                "grant_type":    "refresh_token",
                "refresh_token": AMZ_REFRESH_TOKEN,
                "client_id":     AMZ_APP_ID,
                "client_secret": AMZ_CLIENT_SECRET,
            },
            timeout=30,
        )
        d = r.json()
        return d.get("access_token")
    except Exception as e:
        logger.error(f"Amazon auth error: {e}")
        return None


def _get_tracking(amz_token: str, order_id: str) -> dict | None:
    """Fetch tracking number from Amazon SP-API for a single order."""
    def _fetch():
        return requests.get(
            f"{AMZ_ORDERS_URL}/{order_id}",
            headers={"x-amz-access-token": amz_token},
            params={"includedData": "PACKAGES"},
            timeout=30,
        )

    r = _fetch()
    if r.status_code == 429:
        logger.warning(f"Rate limited on {order_id}, retrying in 30s...")
        time.sleep(30)
        r = _fetch()

    if r.status_code != 200:
        logger.error(f"Amazon order fetch failed: {r.status_code} {r.text[:200]}")
        return None

    try:
        body  = r.json()
        order = body.get("order", body)
    except Exception:
        return None

    # Check order-level packages
    for pkg in order.get("packages", []):
        tn = (pkg.get("trackingNumber") or "").strip()
        if tn:
            return {
                "tracking_number": tn,
                "carrier": (pkg.get("carrier") or "Amazon Shipping").strip(),
            }

    # Check orderItems[].packages[]
    for item in order.get("orderItems", []):
        for pkg in item.get("packages", []):
            tn = (pkg.get("trackingNumber") or "").strip()
            if tn:
                return {
                    "tracking_number": tn,
                    "carrier": (pkg.get("carrier") or "Amazon Shipping").strip(),
                }

    return None


# ════════════════════════════════════════════════════════════════
# HELPERS — Linnworks
# ════════════════════════════════════════════════════════════════

def _auth_linnworks() -> tuple[str | None, str | None]:
    try:
        r = requests.post(
            "https://api.linnworks.net/api/Auth/AuthorizeByApplication",
            json={
                "ApplicationId":     LW_APP_ID,
                "ApplicationSecret": LW_APP_SECRET,
                "Token":             LW_TOKEN,
            },
            timeout=30,
        )
        if r.status_code == 200:
            d = r.json()
            return d["Token"], d["Server"]
    except Exception as e:
        logger.error(f"Linnworks auth error: {e}")
    return None, None


def _find_lw_order(lw_token: str, server: str, amazon_order_id: str) -> dict | None:
    """Search all fulfilment centres for the order."""
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
                ref = (gi.get("ReferenceNum") or "").strip()
                if ref == amazon_order_id:
                    si                = order.get("ShippingInfo", {})
                    existing_tracking = (si.get("TrackingNumber") or "").strip()
                    return {
                        "lw_guid":           order.get("OrderId", ""),
                        "shipping_info":     si,
                        "existing_tracking": existing_tracking,
                        "fulfilment_centre": fc_id,
                    }
        except Exception as e:
            logger.error(f"LW search error (fc={fc_id}): {e}")

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
