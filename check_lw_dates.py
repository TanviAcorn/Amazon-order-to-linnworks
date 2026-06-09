"""Check if a specific order has been updated in Linnworks."""
import json, requests
from common import auth_linnworks, find_lw_order

lw_token, lw_server = auth_linnworks()
ORDER_ID = "204-8911269-4147505"

print(f"\nLooking up {ORDER_ID} in Linnworks...")
result = find_lw_order(lw_token, lw_server, ORDER_ID)

if result:
    tracking = result["existing_tracking"] or "(none)"
    print(f"  Found!  LW GUID : {result['lw_guid']}")
    print(f"  Tracking        : {tracking}")
    if result["existing_tracking"]:
        print(f"  ✅ Tracking is already set in Linnworks!")
    else:
        print(f"  ⏳ No tracking yet — Azure timer will update within 5 minutes")
else:
    print(f"  ❌ Order not found in Linnworks open orders")
