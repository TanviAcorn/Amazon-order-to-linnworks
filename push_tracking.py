"""
push_tracking.py — Step 2: Push tracking for a single order into Linnworks
───────────────────────────────────────────────────────────────────────────
Reads the CSV produced by fetch_orders.py, lets you type the Amazon Order ID
you've verified in Linnworks, then updates that one order's tracking number.

Usage:
    python push_tracking.py
    python push_tracking.py --order-id 123-4567890-1234567
    python push_tracking.py --csv orders/orders_2026-06-07.csv --order-id 123-4567890-1234567
"""

import os, csv, sys, argparse
from datetime import datetime, timedelta
from common import auth_linnworks, find_lw_order, write_tracking_to_lw

os.makedirs("logs", exist_ok=True)


# ════════════════════════════════════════════════════════════════
# CSV HELPERS
# ════════════════════════════════════════════════════════════════

def find_latest_csv():
    """Return the most recently created CSV in the orders/ folder."""
    orders_dir = "orders"
    if not os.path.isdir(orders_dir):
        return None

    csvs = sorted(
        [f for f in os.listdir(orders_dir) if f.endswith(".csv")],
        reverse=True,
    )
    if not csvs:
        return None

    return os.path.join(orders_dir, csvs[0])


def load_csv(csv_path):
    """
    Load orders from the CSV file.
    Returns a dict keyed by amazon_order_id.
    """
    orders = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            oid = row.get("amazon_order_id", "").strip()
            if oid:
                orders[oid] = row
    return orders


def print_csv_table(orders):
    """Pretty-print the orders from the CSV."""
    print(f"\n  {'#':<4} {'Amazon Order ID':<22} {'Tracking Number':<30} {'Carrier':<20} {'Date'}")
    print("  " + "-" * 90)
    for i, (oid, row) in enumerate(orders.items(), 1):
        print(f"  {i:<4} {oid:<22} {row['tracking_number']:<30} "
              f"{row['carrier']:<20} {row.get('purchase_date','')}")


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Push tracking for a single Amazon order into Linnworks"
    )
    parser.add_argument(
        "--csv",
        help="Path to the orders CSV (defaults to latest in orders/ folder)",
        default=None,
    )
    parser.add_argument(
        "--order-id",
        help="Amazon Order ID to update (skips the interactive prompt)",
        default=None,
    )
    args = parser.parse_args()

    print("=" * 65)
    print("  push_tracking.py  —  Update single order tracking in Linnworks")
    print(f"  Run time : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # ── Locate the CSV ────────────────────────────────────────
    csv_path = args.csv or find_latest_csv()

    if not csv_path or not os.path.isfile(csv_path):
        print("\n  ❌ No orders CSV found.")
        print("     Run fetch_orders.py first to generate the CSV,")
        print("     or pass --csv path/to/file.csv")
        sys.exit(1)

    print(f"\n  Using CSV : {csv_path}")

    orders = load_csv(csv_path)

    if not orders:
        print("  ❌ CSV is empty or has no valid rows.")
        sys.exit(1)

    # ── Show the orders table ──────────────────────────────────
    print_csv_table(orders)

    # ── Pick the order ─────────────────────────────────────────
    if args.order_id:
        target_id = args.order_id.strip()
        print(f"\n  Using order ID from argument: {target_id}")
    else:
        print()
        target_id = input("  Enter the Amazon Order ID to update in Linnworks: ").strip()

    if not target_id:
        print("  ❌ No order ID entered. Exiting.")
        sys.exit(1)

    if target_id not in orders:
        print(f"\n  ❌ '{target_id}' not found in the CSV.")
        print("     Make sure you copied it exactly (including dashes).")
        sys.exit(1)

    row             = orders[target_id]
    tracking_number = row["tracking_number"]
    carrier         = row["carrier"]

    print(f"\n  Order     : {target_id}")
    print(f"  Tracking  : {tracking_number}")
    print(f"  Carrier   : {carrier}")

    # ── Confirm before writing ─────────────────────────────────
    confirm = input("\n  Proceed to update this order in Linnworks? [y/N]: ").strip().lower()
    if confirm != "y":
        print("  Aborted.")
        sys.exit(0)

    # ── Authenticate with Linnworks ────────────────────────────
    lw_token, lw_server = auth_linnworks()
    if not lw_token:
        sys.exit(1)

    # ── Find the order in Linnworks ────────────────────────────
    print(f"\n  Looking up {target_id} in Linnworks...")
    lw_order = find_lw_order(lw_token, lw_server, target_id)

    if lw_order is None:
        print(f"\n  ❌ Order {target_id} not found in Linnworks open orders.")
        print("     It may already be processed or the reference number differs.")
        sys.exit(1)

    if lw_order["has_tracking"]:
        print(f"\n  ⚠️  This order already has tracking: {lw_order['existing_tracking']}")
        overwrite = input("     Overwrite with the new tracking number? [y/N]: ").strip().lower()
        if overwrite != "y":
            print("  Aborted.")
            sys.exit(0)

    # ── Write the tracking ─────────────────────────────────────
    print(f"\n  Writing tracking → {tracking_number} ...")

    ok = write_tracking_to_lw(
        lw_token, lw_server,
        lw_order["lw_guid"],
        tracking_number, carrier,
        lw_order["shipping_info"],
    )

    print("\n" + "=" * 65)
    if ok:
        print(f"  ✅ SUCCESS")
        print(f"  Order     : {target_id}")
        print(f"  LW GUID   : {lw_order['lw_guid']}")
        print(f"  Tracking  : {tracking_number}")
        print(f"  Carrier   : {carrier}")
        print()
        print("  Refresh Linnworks open orders — the tracking badge")
        print("  should now be set and the order ready for pickwave.")
    else:
        print(f"  ❌ FAILED to write tracking.")
        print(f"  Check: logs/set_tracking_{lw_order['lw_guid']}.json")
    print("=" * 65)


if __name__ == "__main__":
    main()
