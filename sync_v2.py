"""
sync_v2.py — Batch tracking sync: Amazon (previous day) → Linnworks
─────────────────────────────────────────────────────────────────────
Flow:
  1. Fetch yesterday's Amazon orders that have a tracking number
     (i.e. a shipping label has been printed / carrier has been assigned).
  2. For each such order, find the matching open order in Linnworks
     using the Amazon Order ID stored in Linnworks as ReferenceNum.
  3. If the Linnworks order has no tracking yet, write the tracking number.
  4. Log everything so you can audit what was updated.

This makes the orders ready for pickwave processing in Linnworks.
"""

import os, json, requests, time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# ── Amazon SP-API credentials ──────────────────────────────────
AMZ_APP_ID        = os.getenv("ASL_LWA_APP_ID")
AMZ_CLIENT_SECRET = os.getenv("ASL_LWA_CLIENT_SECRET")
AMZ_REFRESH_TOKEN = os.getenv("ASL_LWA_REFRESH_TOKEN")

# ── Linnworks credentials ──────────────────────────────────────
LW_CLIENT_ID      = os.getenv("LINNWORKS_CLIENT_ID")
LW_CLIENT_SECRET  = os.getenv("LINNWORKS_CLIENT_SECRET")
LW_TOKEN          = os.getenv("LINNWORKS_TOKEN")

# ── Amazon SP-API base URL (EU region) ────────────────────────
AMZ_ORDERS_URL    = "https://sellingpartnerapi-eu.amazon.com/orders/2026-01-01/orders"

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
    """Authenticate with Linnworks and return (session_token, server_url)."""
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
# STEP 1 — Fetch yesterday's Amazon orders that have tracking
#
# We call the Amazon Orders API with:
#   • LastUpdatedAfter  = yesterday 00:00:00 UTC
#   • LastUpdatedBefore = today    00:00:00 UTC
#   • OrderStatuses     = Shipped  (only shipped orders carry tracking)
#
# "Shipped" status on Amazon means a shipping label has been created
# and tracking has been assigned — exactly what we want.
# ════════════════════════════════════════════════════════════════

def step1_get_amazon_orders_with_tracking(amz_token):
    """
    Returns a list of dicts:
      { amazon_order_id, tracking_number, carrier }
    for all orders updated yesterday that already have tracking.
    """
    print("\n[STEP 1] Fetching yesterday's Amazon orders with tracking...")

    # Build yesterday's date window in ISO-8601 UTC
    today_utc     = datetime.now(timezone.utc).replace(
                        hour=0, minute=0, second=0, microsecond=0)
    yesterday_utc = today_utc - timedelta(days=1)

    created_after  = yesterday_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    created_before = today_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"  Window: {created_after}  →  {created_before}")

    orders_with_tracking = []
    next_token           = None
    page                 = 0

    while True:
        page += 1
        params = {
            "MarketplaceIds": "A1F83G8C2ARO7P",   # Amazon.co.uk marketplace ID
            "OrderStatuses":  "Shipped",            # only shipped = label printed
        }

        if next_token:
            # Subsequent pages use NextToken instead of date filters
            params = {
                "MarketplaceIds": "A1F83G8C2ARO7P",
                "NextToken":      next_token,
            }
        else:
            # First page — use date range
            params["LastUpdatedAfter"]  = created_after
            params["LastUpdatedBefore"] = created_before

        r = requests.get(
            AMZ_ORDERS_URL,
            headers={"x-amz-access-token": amz_token},
            params=params,
            timeout=30,
        )

        # Save raw page for debugging
        log_file = f"logs/amz_orders_page{page}.json"
        try:
            json.dump({"status": r.status_code, "body": r.json()}, open(log_file, "w"), indent=2)
        except Exception:
            open(log_file, "w").write(r.text)

        if r.status_code == 429:
            print("  ⚠️  Rate-limited by Amazon, waiting 60 s...")
            time.sleep(60)
            continue

        if r.status_code != 200:
            print(f"  ❌ Amazon Orders API failed (page {page}): {r.status_code} {r.text[:300]}")
            break

        body    = r.json()
        payload = body.get("payload", {})
        orders  = payload.get("Orders", [])

        print(f"  Page {page}: {len(orders)} order(s)")

        for order in orders:
            amazon_order_id = order.get("AmazonOrderId", "").strip()
            status          = order.get("OrderStatus", "")

            if not amazon_order_id:
                continue

            # Fetch shipment / package details to get the tracking number
            tracking_info = _get_tracking_for_order(amz_token, amazon_order_id)

            if tracking_info:
                orders_with_tracking.append({
                    "amazon_order_id": amazon_order_id,
                    "tracking_number": tracking_info["tracking_number"],
                    "carrier":         tracking_info["carrier"],
                    "order_status":    status,
                })
                print(f"  ✅ {amazon_order_id} → tracking: {tracking_info['tracking_number']}")
            else:
                print(f"  ⏭️  {amazon_order_id} — no tracking yet, skipping")

            # Be polite to the Amazon API — avoid throttling
            time.sleep(0.5)

        next_token = payload.get("NextToken")
        if not next_token:
            break   # No more pages

    print(f"\n  Total orders with tracking: {len(orders_with_tracking)}")
    return orders_with_tracking


def _get_tracking_for_order(amz_token, amazon_order_id):
    """
    Fetch the tracking number for a single Amazon order.
    Returns { tracking_number, carrier } or None if not available.
    """
    r = requests.get(
        f"{AMZ_ORDERS_URL}/{amazon_order_id}",
        headers={"x-amz-access-token": amz_token},
        params={"includedData": "PACKAGES"},
        timeout=30,
    )

    if r.status_code == 429:
        print(f"    ⚠️  Rate-limited fetching {amazon_order_id}, waiting 30 s...")
        time.sleep(30)
        # Retry once
        r = requests.get(
            f"{AMZ_ORDERS_URL}/{amazon_order_id}",
            headers={"x-amz-access-token": amz_token},
            params={"includedData": "PACKAGES"},
            timeout=30,
        )

    if r.status_code != 200:
        return None

    try:
        body = r.json()
    except Exception:
        return None

    order    = body.get("order", body)
    packages = order.get("packages", [])

    for pkg in packages:
        tn = (pkg.get("trackingNumber") or "").strip()
        if tn:
            carrier = (pkg.get("carrier") or "Amazon Shipping").strip()
            return {"tracking_number": tn, "carrier": carrier}

    # Fall back: some older responses nest tracking under order-level fields
    tn = (order.get("TrackingNumber") or order.get("trackingNumber") or "").strip()
    if tn:
        return {"tracking_number": tn, "carrier": "Amazon Shipping"}

    return None


# ════════════════════════════════════════════════════════════════
# STEP 2 — Find matching order in Linnworks by Amazon Order ID
#
# Linnworks stores the Amazon order reference in GeneralInfo.ReferenceNum
# We search open orders where Source=Amazon AND ReferenceNum=<amazon_id>
# ════════════════════════════════════════════════════════════════

def step2_find_lw_order(lw_token, server, amazon_order_id):
    """
    Look up the Linnworks open order whose ReferenceNum matches
    the given Amazon Order ID.

    Returns { lw_guid, shipping_info, has_tracking } or None.
    """
    lw_filter = json.dumps({
        "TextFields": [
            {
                "FieldCode": "GENERAL_INFO_SOURCE",
                "Type":      0,        # 0 = Equals
                "Text":      "Amazon",
            },
            {
                "FieldCode": "GENERAL_INFO_REFERENCE_NUM",
                "Type":      0,        # 0 = Equals
                "Text":      amazon_order_id,
            },
        ]
    })

    r = requests.post(
        f"{server}/api/Orders/GetOpenOrders",
        headers={"Authorization": lw_token},
        data={
            "entriesPerPage":   10,
            "pageNumber":       1,
            "filters":          lw_filter,
            "sorting":          json.dumps([{"Direction": 1, "FieldCode": "GENERAL_INFO_DATE"}]),
            "fulfilmentCenter": "00000000-0000-0000-0000-000000000000",
            "additionalFilter": "",
        },
        timeout=30,
    )

    if r.status_code != 200:
        print(f"    ❌ GetOpenOrders failed for {amazon_order_id}: {r.status_code}")
        return None

    data    = r.json()
    entries = data.get("Data", [])

    if not entries:
        print(f"    ⚠️  {amazon_order_id} not found in Linnworks open orders (may be processed already)")
        return None

    order   = entries[0]
    si      = order.get("ShippingInfo", {})
    lw_guid = order.get("OrderId", "")
    existing_tracking = (si.get("TrackingNumber") or "").strip()

    return {
        "lw_guid":       lw_guid,
        "shipping_info": si,
        "has_tracking":  bool(existing_tracking),
        "existing_tracking": existing_tracking,
    }


# ════════════════════════════════════════════════════════════════
# STEP 3 — Write tracking number to Linnworks order
#
# Preserves all existing ShippingInfo fields (PostalServiceId etc.)
# and only overwrites TrackingNumber.
# ════════════════════════════════════════════════════════════════

def step3_write_tracking(lw_token, server, lw_guid, tracking_number,
                         carrier, existing_si):
    """
    Update the TrackingNumber on an existing Linnworks order.
    All other shipping fields are preserved from the existing record.
    Returns True on success.
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

    # Save response for audit
    log_file = f"logs/set_tracking_{lw_guid}.json"
    try:
        json.dump({"status": r.status_code, "body": r.json()}, open(log_file, "w"), indent=2)
    except Exception:
        open(log_file, "w").write(r.text)

    return r.status_code == 200


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print("=" * 65)
    print("  Amazon → Linnworks  [batch tracking sync]")
    print(f"  Run time  : {run_time}")
    print(f"  Syncing   : orders from {yesterday} (previous day)")
    print("=" * 65)

    # ── Authenticate ──────────────────────────────────────────
    amz_token = auth_amazon()
    if not amz_token:
        return

    lw_token, lw_server = auth_linnworks()
    if not lw_token:
        return

    # ── Step 1: Get all yesterday's Amazon orders with tracking ──
    amazon_orders = step1_get_amazon_orders_with_tracking(amz_token)

    if not amazon_orders:
        print("\n  ✅ No Amazon orders with tracking found for yesterday.")
        print("     Nothing to sync.")
        return

    # ── Steps 2 & 3: Match + update each order in Linnworks ───
    print(f"\n[STEP 2+3] Matching {len(amazon_orders)} order(s) in Linnworks and writing tracking...\n")

    results = {
        "updated":       [],
        "already_has":   [],
        "not_in_lw":     [],
        "failed":        [],
    }

    for item in amazon_orders:
        amazon_id = item["amazon_order_id"]
        tracking  = item["tracking_number"]
        carrier   = item["carrier"]

        print(f"  ── {amazon_id}  ({carrier}: {tracking})")

        # Find the order in Linnworks
        lw_order = step2_find_lw_order(lw_token, lw_server, amazon_id)

        if lw_order is None:
            results["not_in_lw"].append(amazon_id)
            continue

        if lw_order["has_tracking"]:
            print(f"    ⏭️  Already has tracking: {lw_order['existing_tracking']} — skipping")
            results["already_has"].append(amazon_id)
            continue

        # Write the tracking number
        ok = step3_write_tracking(
            lw_token, lw_server,
            lw_order["lw_guid"],
            tracking, carrier,
            lw_order["shipping_info"],
        )

        if ok:
            print(f"    ✅ Tracking written → {tracking}")
            results["updated"].append(amazon_id)
        else:
            print(f"    ❌ Failed to write tracking for {amazon_id}")
            results["failed"].append(amazon_id)

        # Small pause to avoid hammering Linnworks
        time.sleep(0.2)

    # ── Summary ───────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("  SUMMARY")
    print("=" * 65)
    print(f"  ✅ Updated              : {len(results['updated'])}")
    print(f"  ⏭️  Already had tracking : {len(results['already_has'])}")
    print(f"  ⚠️  Not found in LW      : {len(results['not_in_lw'])}")
    print(f"  ❌ Failed               : {len(results['failed'])}")

    if results["updated"]:
        print(f"\n  Updated order IDs:")
        for oid in results["updated"]:
            print(f"    • {oid}")

    if results["not_in_lw"]:
        print(f"\n  Orders not found in Linnworks (check manually):")
        for oid in results["not_in_lw"]:
            print(f"    • {oid}")

    if results["failed"]:
        print(f"\n  Failed orders (check logs/ folder):")
        for oid in results["failed"]:
            print(f"    • {oid}")

    # Save summary log
    summary_file = f"logs/sync_summary_{yesterday}.json"
    json.dump({
        "run_time":  run_time,
        "sync_date": yesterday,
        "results":   results,
    }, open(summary_file, "w"), indent=2)
    print(f"\n  Summary saved → {summary_file}")
    print("=" * 65)


if __name__ == "__main__":
    main()
