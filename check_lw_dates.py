"""Quick check: find the specific 08/06/2026 orders from the screenshot."""
import json, requests
from common import auth_linnworks

lw_token, lw_server = auth_linnworks()

# The two order IDs visible in the Linnworks screenshot
test_ids = ["203-1215893-4993907", "202-2904411-9474719"]

print("\nSearching for the two orders from the screenshot...\n")

for order_id in test_ids:
    r = requests.post(
        f"{lw_server}/api/Orders/GetOpenOrders",
        headers={"Authorization": lw_token},
        data={
            "entriesPerPage":   10,
            "pageNumber":       1,
            "filters":          json.dumps({"TextFields": [{"FieldCode": "GENERAL_INFO_SOURCE", "Type": 0, "Text": "Amazon"}]}),
            "sorting":          json.dumps([{"Direction": 0, "FieldCode": "GENERAL_INFO_DATE"}]),
            "fulfilmentCenter": "00000000-0000-0000-0000-000000000000",
            "additionalFilter": order_id,
        },
        timeout=30,
    )
    data    = r.json()
    entries = data.get("Data", [])
    total   = data.get("TotalEntries", 0)

    print(f"  Search for: {order_id}")
    print(f"  Status: {r.status_code}  |  Matches: {total}")

    for o in entries:
        gi = o.get("GeneralInfo", {})
        si = o.get("ShippingInfo", {})
        print(f"    ✅ Found!")
        print(f"       LW GUID     : {o.get('OrderId')}")
        print(f"       Source      : {gi.get('Source')}")
        print(f"       ReferenceNum: {gi.get('ReferenceNum')}")
        print(f"       ReceivedDate: {(gi.get('ReceivedDate') or '')[:10]}")
        print(f"       Tracking    : {si.get('TrackingNumber') or '(none)'}")
        print(f"       PostalSvc   : {si.get('PostalServiceName')}")
        print(f"       PostalSvcId : {si.get('PostalServiceId')}")
    if not entries:
        print(f"    ❌ Not found via additionalFilter")
    print()
