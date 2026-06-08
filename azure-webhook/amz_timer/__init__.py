"""
amz_timer/__init__.py — Timer trigger: runs every 5 minutes
────────────────────────────────────────────────────────────
Polls Amazon for orders that moved to SHIPPED in the last 10 minutes,
fetches their tracking numbers, and writes them to Linnworks.

The 10-minute window (vs 5-minute run interval) gives overlap so no
order is ever missed even if a run is slightly delayed.
"""

import json, logging, os, time
from datetime import datetime, timedelta, timezone
import azure.functions as func
import requests

logger = logging.getLogger("amz_lw_timer")

# ── Credentials ───────────────────────────────────────────────
AMZ_APP_ID        = os.environ["ASL_LWA_APP_ID"]
AMZ_CLIENT_SECRET = os.environ["ASL_LWA_CLIENT_SECRET"]
AMZ_REFRESH_TOKEN = os.environ["ASL_LWA_REFRESH_TOKEN"]
LW_APP_ID         = os.environ.get("LINNWORKS_APP_ID")     or os.environ.get("LINNWORKS_CLIENT_ID")
LW_APP_SECRET     = os.environ.get("LINNWORKS_APP_SECRET") or os.environ.get("LINNWORKS_CLIENT_SECRET")
LW_TOKEN          = os.environ["LINNWORKS_TOKEN"]

AMZ_ORDERS_URL = "https://sellingpartnerapi-eu.amazon.com/orders/2026-01-01/orders"
MARKETPLACE_ID = "A1F83G8C2ARO7P"

FULFILMENT_CENTRES = [
    "00000000-0000-0000-0000-000000000000",
    "64e6b463-fbaa-490f-b3ec-2e53eb197e2d",
    "8e8ac5eb-ebf7-485a-9545-0766dbf76ada",
    "db86ec44-ff4f-4d25-82c1-ff0a488c005d",
    "9799d0f6-684c-4b61-93fc-8244ae0ced58",
    "733af7e9-3f17-465f-8ca1-cc5951468453",   # TFC
    "d005ecc9-1dcf-4ccc-80c0-8844d0dcab95",
]

# Look back 10 minutes — covers any delay between runs
LOOKBACK_MINUTES = 10


def main(timer: func.TimerRequest) -> None:
    run_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info(f"Timer fired at {run_time}")

    if timer.past_due:
        logger.warning("Timer is running late")

    # ── Auth ──────────────────────────────────────────────────
    amz_token = _auth_amazon()
    if not amz_token:
        logger.error("Amazon auth failed — aborting run")
        return

    lw_token, lw_server = _auth_linnworks()
    if not lw_token:
        logger.error("Linnworks auth failed — aborting run")
        return

    # ── Fetch recently shipped Amazon orders ──────────────────
    since = (datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES))
    after  = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    before = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.info(f"Polling Amazon: lastUpdatedAfter={after}")

    orders = _fetch_shipped_orders(amz_token, after, before)
    logger.info(f"Found {len(orders)} shipped order(s) in window")

    if not orders:
        logger.info("Nothing to sync")
        return

    # ── Process each order ────────────────────────────────────
    updated = skipped = errors = 0

    for order_id, tracking_info in orders.items():
        try:
            lw_order = _find_lw_order(lw_token, lw_server, order_id)

            if not lw_order:
                logger.info(f"  {order_id} — not in Linnworks, skipping")
                skipped += 1
                continue

            if lw_order["existing_tracking"] == tracking_info["tracking_number"]:
                logger.info(f"  {order_id} — tracking already up to date")
                skipped += 1
                continue

            ok = _write_tracking(
                lw_token, lw_server,
                lw_order["lw_guid"],
                tracking_info["tracking_number"],
                tracking_info["carrier"],
                lw_order["shipping_info"],
            )

            if ok:
                logger.info(f"  ✅ {order_id} → {tracking_info['tracking_number']} ({tracking_info['carrier']})")
                updated += 1
            else:
                logger.error(f"  ❌ {order_id} — Linnworks write failed")
                errors += 1

        except Exception as e:
            logger.error(f"  ❌ {order_id} — exception: {e}")
            errors += 1

        time.sleep(0.3)  # gentle rate limiting

    logger.info(f"Run complete — updated={updated} skipped={skipped} errors={errors}")


# ════════════════════════════════════════════════════════════════
# AMAZON
# ════════════════════════════════════════════════════════════════

def _auth_amazon():
    try:
        r = requests.post(
            "https://api.amazon.com/auth/o2/token",
            data={"grant_type":    "refresh_token",
                  "refresh_token": AMZ_REFRESH_TOKEN,
                  "client_id":     AMZ_APP_ID,
                  "client_secret": AMZ_CLIENT_SECRET},
            timeout=30,
        )
        return r.json().get("access_token")
    except Exception as e:
        logger.error(f"Amazon auth error: {e}")
        return None


def _fetch_shipped_orders(amz_token, after, before):
    """
    Fetch all SHIPPED orders updated in [after, before].
    Returns dict: { amazon_order_id: { tracking_number, carrier } }
    Only includes orders that actually have a tracking number.
    """
    results        = {}
    pagination_token = None
    page           = 0

    while True:
        page += 1

        if pagination_token:
            params = {
                "marketplaceIds":  MARKETPLACE_ID,
                "paginationToken": pagination_token,
                "includedData":    "PACKAGES,FULFILLMENT",
            }
        else:
            params = {
                "marketplaceIds":      MARKETPLACE_ID,
                "fulfillmentStatuses": "SHIPPED",
                "lastUpdatedAfter":    after,
                "lastUpdatedBefore":   before,
                "includedData":        "PACKAGES,FULFILLMENT",
                "maxResultsPerPage":   100,
            }

        try:
            r = requests.get(
                AMZ_ORDERS_URL,
                headers={"x-amz-access-token": amz_token},
                params=params,
                timeout=30,
            )
        except Exception as e:
            logger.error(f"Amazon fetch error page {page}: {e}")
            break

        if r.status_code == 429:
            logger.warning("Amazon rate limit — waiting 30s")
            time.sleep(30)
            continue

        if r.status_code != 200:
            logger.error(f"Amazon API error {r.status_code}: {r.text[:200]}")
            break

        body   = r.json()
        orders = body.get("orders", [])

        for order in orders:
            order_id = order.get("orderId", "").strip()
            if not order_id:
                continue

            tracking = _extract_tracking(order)
            if tracking:
                results[order_id] = tracking

        pagination_token = (body.get("pagination") or {}).get("nextToken")
        if not pagination_token:
            break

    return results


def _extract_tracking(order):
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
# LINNWORKS
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
