"""
push_one.py — Fetch tracking from Amazon and push it to Linnworks
              for a single order ID, without needing a CSV.
"""
import sys, json, requests
from common import (auth_amazon, auth_linnworks,
                    get_tracking_for_order, find_lw_order, write_tracking_to_lw,
                    AMZ_ORDERS_URL)

ORDER_ID = "203-3930225-5403561"

print("=" * 60)
print(f"  Syncing: {ORDER_ID}")
print("=" * 60)

# ── 1. Get Amazon tracking ────────────────────────────────────
amz_token = auth_amazon()
if not amz_token:
    sys.exit(1)

tracking = get_tracking_for_order(amz_token, ORDER_ID)
if not tracking:
    print("  ❌ No tracking found on Amazon for this order yet.")
    sys.exit(1)

print(f"\n  ✅ Amazon tracking found:")
print(f"     Tracking : {tracking['tracking_number']}")
print(f"     Carrier  : {tracking['carrier']}")

# ── 2. Find in Linnworks ──────────────────────────────────────
lw_token, lw_server = auth_linnworks()
if not lw_token:
    sys.exit(1)

print(f"\n  Looking up {ORDER_ID} in Linnworks...")
lw_order = find_lw_order(lw_token, lw_server, ORDER_ID)

if not lw_order:
    print(f"  ❌ Order not found in Linnworks open orders.")
    sys.exit(1)

print(f"  ✅ Found in Linnworks: {lw_order['lw_guid']}")

if lw_order["has_tracking"]:
    print(f"  ⚠️  Already has tracking: {lw_order['existing_tracking']}")
    ans = input("     Overwrite? [y/N]: ").strip().lower()
    if ans != "y":
        print("  Aborted.")
        sys.exit(0)

# ── 3. Write tracking ─────────────────────────────────────────
print(f"\n  Writing tracking → {tracking['tracking_number']} ...")
ok = write_tracking_to_lw(
    lw_token, lw_server,
    lw_order["lw_guid"],
    tracking["tracking_number"],
    tracking["carrier"],
    lw_order["shipping_info"],
)

print("\n" + "=" * 60)
if ok:
    print(f"  ✅ SUCCESS")
    print(f"  Order    : {ORDER_ID}")
    print(f"  Tracking : {tracking['tracking_number']}")
    print(f"  Carrier  : {tracking['carrier']}")
    print(f"  → Refresh Linnworks — order is ready for pickwave.")
else:
    print(f"  ❌ Failed — check logs/set_tracking_{lw_order['lw_guid']}.json")
print("=" * 60)
