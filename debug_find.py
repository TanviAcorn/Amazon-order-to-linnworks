"""Debug: search each fulfilment centre for the order."""
import json, requests
from common import auth_linnworks

lw_token, lw_server = auth_linnworks()
ORDER_ID = "203-3930225-5403561"

LOCATIONS = {
    "00000000-0000-0000-0000-000000000000": "Default",
    "64e6b463-fbaa-490f-b3ec-2e53eb197e2d": "ASL Amazon FBA",
    "8e8ac5eb-ebf7-485a-9545-0766dbf76ada": "ASL-TFC",
    "db86ec44-ff4f-4d25-82c1-ff0a488c005d": "Jambo Amazon FBA",
    "9799d0f6-684c-4b61-93fc-8244ae0ced58": "Non-WMS managed",
    "733af7e9-3f17-465f-8ca1-cc5951468453": "TFC",
    "d005ecc9-1dcf-4ccc-80c0-8844d0dcab95": "TFC-FBA",
}

print(f"\nSearching all fulfilment centres for: {ORDER_ID}\n")

for loc_id, loc_name in LOCATIONS.items():
    r = requests.post(
        f"{lw_server}/api/Orders/GetOpenOrders",
        headers={"Authorization": lw_token},
        data={
            "entriesPerPage":   10,
            "pageNumber":       1,
            "filters":          "",
            "sorting":          json.dumps([{"Direction": 0, "FieldCode": "GENERAL_INFO_DATE"}]),
            "fulfilmentCenter": loc_id,
            "additionalFilter": ORDER_ID,
        },
        timeout=30,
    )
    total   = r.json().get("TotalEntries", 0) if r.status_code == 200 else "ERR"
    entries = r.json().get("Data", [])        if r.status_code == 200 else []

    marker = "✅ FOUND" if entries else f"  matches={total}"
    print(f"  [{loc_name}]  {marker}")

    for o in entries:
        gi = o.get("GeneralInfo", {})
        si = o.get("ShippingInfo", {})
        print(f"    OrderId  : {o.get('OrderId')}")
        print(f"    Ref      : {gi.get('ReferenceNum')}")
        print(f"    Date     : {(gi.get('ReceivedDate') or '')[:10]}")
        print(f"    Tracking : {si.get('TrackingNumber') or '(none)'}")
        print(f"    Source   : {gi.get('Source')}  /  {gi.get('SubSource')}")
