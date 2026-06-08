"""
common.py — Shared auth and helpers for Amazon → Linnworks sync
"""

import os, json, requests, time
from dotenv import load_dotenv

load_dotenv()

# ── Amazon SP-API credentials ──────────────────────────────────
AMZ_APP_ID        = os.getenv("ASL_LWA_APP_ID")
AMZ_CLIENT_SECRET = os.getenv("ASL_LWA_CLIENT_SECRET")
AMZ_REFRESH_TOKEN = os.getenv("ASL_LWA_REFRESH_TOKEN")

# ── Linnworks credentials ─────────────────────────────────────
# .env uses APP_ID / APP_SECRET — support both naming conventions
LW_CLIENT_ID      = os.getenv("LINNWORKS_APP_ID")     or os.getenv("LINNWORKS_CLIENT_ID")
LW_CLIENT_SECRET  = os.getenv("LINNWORKS_APP_SECRET") or os.getenv("LINNWORKS_CLIENT_SECRET")
LW_TOKEN          = os.getenv("LINNWORKS_TOKEN")

FULFILMENT_CENTRES = [
    "00000000-0000-0000-0000-000000000000",   # Default
    "64e6b463-fbaa-490f-b3ec-2e53eb197e2d",   # ASL Amazon FBA
    "8e8ac5eb-ebf7-485a-9545-0766dbf76ada",   # ASL-TFC
    "db86ec44-ff4f-4d25-82c1-ff0a488c005d",   # Jambo Amazon FBA
    "9799d0f6-684c-4b61-93fc-8244ae0ced58",   # Non-WMS managed
    "733af7e9-3f17-465f-8ca1-cc5951468453",   # TFC  ← where current orders live
    "d005ecc9-1dcf-4ccc-80c0-8844d0dcab95",   # TFC-FBA
]

AMZ_ORDERS_URL = "https://sellingpartnerapi-eu.amazon.com/orders/2026-01-01/orders"
MARKETPLACE_ID = "A1F83G8C2ARO7P"   # Amazon.co.uk

os.makedirs("logs", exist_ok=True)


# ════════════════════════════════════════════════════════════════
# AUTH
# ════════════════════════════════════════════════════════════════

def auth_amazon():
    """Exchange refresh token for a short-lived Amazon access token."""
    print("  Getting Amazon token...")
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
    if "access_token" in d:
        print("  ✅ Amazon token OK")
        return d["access_token"]
    print(f"  ❌ Amazon auth failed: {d}")
    return None


def auth_linnworks():
    """Authenticate with Linnworks. Returns (session_token, server_url)."""
    print("  Authenticating with Linnworks...")
    r = requests.post(
        "https://api.linnworks.net/api/Auth/AuthorizeByApplication",
        json={
            "ApplicationId":     LW_CLIENT_ID,
            "ApplicationSecret": LW_CLIENT_SECRET,
            "Token":             LW_TOKEN,
        },
        timeout=30,
    )
    if r.status_code == 200:
        d = r.json()
        print(f"  ✅ Linnworks OK | Server: {d['Server']}")
        return d["Token"], d["Server"]
    print(f"  ❌ Linnworks auth failed: {r.text}")
    return None, None


# ════════════════════════════════════════════════════════════════
# AMAZON HELPERS
# ════════════════════════════════════════════════════════════════

def get_tracking_for_order(amz_token, amazon_order_id):
    """
    Fetch the tracking number for a single Amazon order via the
    v2026-01-01 getOrder endpoint.
    Returns { tracking_number, carrier } or None.
    """
    def _fetch():
        return requests.get(
            f"{AMZ_ORDERS_URL}/{amazon_order_id}",
            headers={"x-amz-access-token": amz_token},
            params={"includedData": "PACKAGES"},
            timeout=30,
        )

    r = _fetch()

    if r.status_code == 429:
        print(f"    ⚠️  Rate-limited fetching {amazon_order_id}, waiting 30 s...")
        time.sleep(30)
        r = _fetch()

    if r.status_code != 200:
        return None

    try:
        body  = r.json()
        order = body.get("order", body)  # unwrap { "order": {...} } envelope
    except Exception:
        return None

    # v2026-01-01: packages[] is at the order level
    for pkg in order.get("packages", []):
        tn = (pkg.get("trackingNumber") or "").strip()
        if tn:
            carrier = (pkg.get("carrier") or "Amazon Shipping").strip()
            return {"tracking_number": tn, "carrier": carrier}

    # Also check inside orderItems[].packages[]
    for item in order.get("orderItems", []):
        for pkg in item.get("packages", []):
            tn = (pkg.get("trackingNumber") or "").strip()
            if tn:
                carrier = (pkg.get("carrier") or "Amazon Shipping").strip()
                return {"tracking_number": tn, "carrier": carrier}

    return None


# ════════════════════════════════════════════════════════════════
# LINNWORKS HELPERS
# ════════════════════════════════════════════════════════════════

def find_lw_order(lw_token, server, amazon_order_id):
    """
    Find the Linnworks open order whose ReferenceNum matches the Amazon
    Order ID. Searches across ALL fulfilment centres since orders can be
    assigned to any of them (TFC, Default, ASL-TFC, etc.).

    Returns:
        { lw_guid, shipping_info, has_tracking (bool), existing_tracking }
        or None if not found in any location.
    """
    for fc_id in FULFILMENT_CENTRES:
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

        entries = r.json().get("Data", [])

        for order in entries:
            gi  = order.get("GeneralInfo", {})
            ref = (gi.get("ReferenceNum") or "").strip()
            if ref == amazon_order_id:
                si                = order.get("ShippingInfo", {})
                existing_tracking = (si.get("TrackingNumber") or "").strip()
                return {
                    "lw_guid":           order.get("OrderId", ""),
                    "shipping_info":     si,
                    "has_tracking":      bool(existing_tracking),
                    "existing_tracking": existing_tracking,
                    "fulfilment_centre": fc_id,
                }

    return None


def write_tracking_to_lw(lw_token, server, lw_guid, tracking_number,
                          carrier, existing_si):
    """
    Write tracking_number to a Linnworks order, preserving all other
    ShippingInfo fields.  Returns True on success.
    """
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

    r = requests.post(
        f"{server}/api/Orders/SetOrderShippingInfo",
        headers={"Authorization": lw_token, "Content-Type": "application/json"},
        json={"orderId": lw_guid, "info": info},
        timeout=30,
    )

    log_file = f"logs/set_tracking_{lw_guid}.json"
    try:
        json.dump({"status": r.status_code, "body": r.json()}, open(log_file, "w"), indent=2)
    except Exception:
        open(log_file, "w").write(r.text)

    return r.status_code == 200
