"""Fetch full details + tracking for a single Amazon order."""
import json, requests
from common import auth_amazon, AMZ_ORDERS_URL

amz_token = auth_amazon()
ORDER_ID  = "203-3930225-5403561"

print(f"\nFetching order: {ORDER_ID}\n")

r = requests.get(
    f"{AMZ_ORDERS_URL}/{ORDER_ID}",
    headers={"x-amz-access-token": amz_token},
    params={"includedData": "PACKAGES,FULFILLMENT,BUYER"},
    timeout=30,
)

print(f"Status: {r.status_code}")

# Save raw response
json.dump({"status": r.status_code, "body": r.json()},
          open(f"logs/order_{ORDER_ID}.json", "w"), indent=2)
print(f"Raw response saved → logs/order_{ORDER_ID}.json\n")

if r.status_code != 200:
    print(f"Error: {r.text}")
else:
    order = r.json().get("order", r.json())  # unwrap { "order": {...} } envelope

    print(f"  Order ID      : {order.get('orderId')}")
    print(f"  Status        : {(order.get('fulfillment') or {}).get('fulfillmentStatus')}")
    print(f"  Created       : {(order.get('createdTime') or '')[:10]}")
    print(f"  Buyer         : {(order.get('buyer') or {}).get('buyerName')}")

    packages = order.get("packages", [])
    print(f"\n  Packages      : {len(packages)}")

    if packages:
        for i, pkg in enumerate(packages, 1):
            print(f"\n  Package {i}:")
            print(f"    Tracking    : {pkg.get('trackingNumber') or '(none)'}")
            print(f"    Carrier     : {pkg.get('carrier') or '(none)'}")
            print(f"    Ship time   : {pkg.get('shipTime') or '(none)'}")
            print(f"    Service     : {pkg.get('shippingService') or '(none)'}")
            status = (pkg.get("packageStatus") or {})
            print(f"    Pkg status  : {status.get('status') or '(none)'}")
    else:
        print("  ⚠️  No packages — label not printed / not shipped yet on Amazon side")

    # Also check inside orderItems in case tracking is nested there
    for item in order.get("orderItems", []):
        for pkg in item.get("packages", []):
            tn = pkg.get("trackingNumber")
            if tn:
                print(f"\n  (Found in orderItem packages) Tracking: {tn}  Carrier: {pkg.get('carrier')}")
