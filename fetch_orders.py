"""
fetch_orders.py — Step 1: Fetch previous day's Amazon orders with tracking
──────────────────────────────────────────────────────────────────────────
Pulls all orders from Amazon that were shipped yesterday (i.e. labels are
printed and tracking is assigned), then saves them to a CSV file for your
manual review in Linnworks.

Output:  orders/orders_YYYY-MM-DD.csv

After reviewing the CSV, run push_tracking.py to update a specific order.
"""

import os, csv, json, time
from datetime import datetime, timedelta, timezone
from common import auth_amazon, AMZ_ORDERS_URL, MARKETPLACE_ID
import requests

os.makedirs("orders", exist_ok=True)
os.makedirs("logs",   exist_ok=True)


def fetch_amazon_orders_with_tracking(amz_token, yesterday_str):
    """
    Fetch all Amazon orders created yesterday with fulfillmentStatus=SHIPPED.
    Tracking is included inline via includedData=PACKAGES — no extra per-order
    calls needed.

    Returns a list of dicts:
        { amazon_order_id, tracking_number, carrier, order_status,
          buyer_name, purchase_date }
    """
    today_utc     = datetime.now(timezone.utc).replace(
                        hour=0, minute=0, second=0, microsecond=0)
    yesterday_utc = today_utc - timedelta(days=1)

    after  = yesterday_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    before = today_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"\n[FETCH] Date window : {after}  →  {before}")

    results          = []
    pagination_token = None
    page             = 0

    while True:
        page += 1

        if pagination_token:
            # Subsequent pages — only token + marketplace needed
            # Do NOT re-send any filter params; Amazon rejects them on paginated calls
            params = {
                "marketplaceIds":  MARKETPLACE_ID,
                "paginationToken": pagination_token,
            }
        else:
            # First page — date range + shipped filter
            params = {
                "marketplaceIds":      MARKETPLACE_ID,
                "fulfillmentStatuses": "SHIPPED",   # v2026-01-01 param name
                "createdAfter":        after,
                "createdBefore":       before,
                "includedData":        "PACKAGES,BUYER,FULFILLMENT",
                "maxResultsPerPage":   100,
            }

        r = requests.get(
            AMZ_ORDERS_URL,
            headers={"x-amz-access-token": amz_token},
            params=params,
            timeout=30,
        )

        # Log raw response for debugging
        log_file = f"logs/amz_orders_{yesterday_str}_page{page}.json"
        try:
            json.dump({"status": r.status_code, "body": r.json()},
                      open(log_file, "w"), indent=2)
        except Exception:
            open(log_file, "w").write(r.text)

        if r.status_code == 429:
            print("  ⚠️  Rate-limited by Amazon — waiting 60 s...")
            time.sleep(60)
            continue

        if r.status_code != 200:
            print(f"  ❌ Amazon Orders API error (page {page}): "
                  f"{r.status_code} {r.text[:300]}")
            break

        body   = r.json()
        orders = body.get("orders", [])

        print(f"  Page {page}: {len(orders)} order(s)")

        for order in orders:
            amazon_order_id = order.get("orderId", "").strip()
            if not amazon_order_id:
                continue

            # Fulfillment status lives inside fulfillment object
            fulfillment   = order.get("fulfillment", {})
            status        = fulfillment.get("fulfillmentStatus", "")
            purchase_date = (order.get("createdTime") or "")[:10]
            buyer_name    = (order.get("buyer", {}).get("buyerName") or "")

            # Tracking is inline in packages[] when includedData=PACKAGES
            tracking_info = _extract_tracking(order)

            if tracking_info:
                results.append({
                    "amazon_order_id": amazon_order_id,
                    "tracking_number": tracking_info["tracking_number"],
                    "carrier":         tracking_info["carrier"],
                    "order_status":    status,
                    "purchase_date":   purchase_date,
                    "buyer_name":      buyer_name,
                })
                print(f"  ✅ {amazon_order_id}  |  {tracking_info['tracking_number']}"
                      f"  ({tracking_info['carrier']})")
            else:
                print(f"  ⏭️  {amazon_order_id} — no tracking yet, skipped")

        pagination_token = (body.get("pagination") or {}).get("nextToken")
        if not pagination_token:
            break

    return results


def _extract_tracking(order):
    """
    Extract the first available tracking number from an order's packages[].
    Returns { tracking_number, carrier } or None.
    """
    # v2026-01-01: packages are at the order level
    for pkg in order.get("packages", []):
        tn = (pkg.get("trackingNumber") or "").strip()
        if tn:
            carrier = (pkg.get("carrier") or "Amazon Shipping").strip()
            return {"tracking_number": tn, "carrier": carrier}

    # Also check inside each orderItem's packages (some responses nest them)
    for item in order.get("orderItems", []):
        for pkg in item.get("packages", []):
            tn = (pkg.get("trackingNumber") or "").strip()
            if tn:
                carrier = (pkg.get("carrier") or "Amazon Shipping").strip()
                return {"tracking_number": tn, "carrier": carrier}

    return None


def save_to_csv(orders, yesterday_str):
    """Write the order list to orders/orders_YYYY-MM-DD.csv."""
    csv_path = os.path.join("orders", f"orders_{yesterday_str}.csv")

    fieldnames = [
        "amazon_order_id",
        "tracking_number",
        "carrier",
        "order_status",
        "purchase_date",
        "buyer_name",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(orders)

    return csv_path


def main():
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print("=" * 65)
    print("  fetch_orders.py  —  Amazon shipped orders → CSV")
    print(f"  Run time  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Fetching  : orders from {yesterday_str} (previous day)")
    print("=" * 65)

    amz_token = auth_amazon()
    if not amz_token:
        return

    orders = fetch_amazon_orders_with_tracking(amz_token, yesterday_str)

    if not orders:
        print("\n  ✅ No Amazon orders with tracking found for yesterday.")
        return

    csv_path = save_to_csv(orders, yesterday_str)

    print("\n" + "=" * 65)
    print(f"  ✅ {len(orders)} order(s) saved to:  {csv_path}")
    print()
    print("  Next steps:")
    print(f"  1. Open {csv_path} and review the orders in Linnworks.")
    print("  2. Run push_tracking.py and enter the Amazon Order ID")
    print("     you want to update.")
    print("=" * 65)


if __name__ == "__main__":
    main()
