"""
debug_lw.py — Diagnose how Amazon orders appear in Linnworks
─────────────────────────────────────────────────────────────
Tries multiple search strategies to find an Amazon order in Linnworks
so we can see exactly what field/value to match on.
"""

import os, json, requests
from dotenv import load_dotenv

load_dotenv()

# Fix: env file uses APP_ID/APP_SECRET, not CLIENT_ID/CLIENT_SECRET
LW_APP_ID     = os.getenv("LINNWORKS_APP_ID")     or os.getenv("LINNWORKS_CLIENT_ID")
LW_APP_SECRET = os.getenv("LINNWORKS_APP_SECRET") or os.getenv("LINNWORKS_CLIENT_SECRET")
LW_TOKEN      = os.getenv("LINNWORKS_TOKEN")

os.makedirs("logs", exist_ok=True)


def auth_linnworks():
    r = requests.post(
        "https://api.linnworks.net/api/Auth/AuthorizeByApplication",
        json={
            "ApplicationId":     LW_APP_ID,
            "ApplicationSecret": LW_APP_SECRET,
            "Token":             LW_TOKEN,
        },
        timeout=30,
    )
    if r.status_code == 200:
        d = r.json()
        print(f"✅ Linnworks auth OK | Server: {d['Server']}")
        return d["Token"], d["Server"]
    print(f"❌ Linnworks auth failed: {r.status_code} {r.text}")
    return None, None


def search_orders(lw_token, server, filters_dict, label=""):
    """Run a GetOpenOrders search and return raw results."""
    # Linnworks expects filters as a flat key=value form string, not JSON TextFields
    # Try both formats
    for fmt_name, filters_val in [
        ("JSON TextFields", json.dumps(filters_dict)),
        ("empty",           ""),
    ]:
        r = requests.post(
            f"{server}/api/Orders/GetOpenOrders",
            headers={"Authorization": lw_token},
            data={
                "entriesPerPage":   5,
                "pageNumber":       1,
                "filters":          filters_val,
                "sorting":          json.dumps([{"Direction": 0, "FieldCode": "GENERAL_INFO_DATE"}]),
                "fulfilmentCenter": "00000000-0000-0000-0000-000000000000",
                "additionalFilter": "",
            },
            timeout=30,
        )
        if r.status_code == 200:
            break

    print(f"\n{'─'*55}")
    print(f"  Search : {label}")
    print(f"  Format : {fmt_name}")
    print(f"  Status : {r.status_code}")
    if r.status_code == 200:
        data    = r.json()
        total   = data.get("TotalEntries", 0)
        entries = data.get("Data", [])
        print(f"  Total matches: {total}")
        for o in entries[:2]:
            gi = o.get("GeneralInfo", {})
            si = o.get("ShippingInfo", {})
            print(f"    OrderId    : {o.get('OrderId')}")
            print(f"    Source     : {gi.get('Source')}")
            print(f"    SubSource  : {gi.get('SubSource')}")
            print(f"    ReferenceNum: {gi.get('ReferenceNum')}")
            print(f"    ExternalRef: {gi.get('ExternalRef')}")
            print(f"    Tracking   : {si.get('TrackingNumber')}")
            print(f"    PostalSvc  : {si.get('PostalServiceName')}")
    else:
        print(f"  Error: {r.text[:300]}")
    return r


def dump_recent_amazon_orders(lw_token, server):
    """Dump the 5 most recent open orders from Amazon source to see their structure."""
    print(f"\n{'='*55}")
    print("  Dumping 5 most recent open Amazon orders (any tracking status)")

    r = requests.post(
        f"{server}/api/Orders/GetOpenOrders",
        headers={"Authorization": lw_token},
        data={
            "entriesPerPage":   5,
            "pageNumber":       1,
            "filters":          json.dumps({
                "TextFields": [
                    {"FieldCode": "GENERAL_INFO_SOURCE", "Type": 0, "Text": "Amazon"},
                ]
            }),
            "sorting":          json.dumps([{"Direction": 0, "FieldCode": "GENERAL_INFO_DATE"}]),
            "fulfilmentCenter": "00000000-0000-0000-0000-000000000000",
            "additionalFilter": "",
        },
        timeout=30,
    )

    if r.status_code != 200:
        print(f"  ❌ Failed: {r.status_code} {r.text[:200]}")
        return

    data    = r.json()
    total   = data.get("TotalEntries", 0)
    entries = data.get("Data", [])
    print(f"  Total Amazon open orders: {total}")

    for o in entries:
        gi = o.get("GeneralInfo", {})
        si = o.get("ShippingInfo", {})
        print(f"\n  ── Order ──────────────────────────────────")
        print(f"    OrderId      : {o.get('OrderId')}")
        print(f"    Source       : {gi.get('Source')}")
        print(f"    SubSource    : {gi.get('SubSource')}")
        print(f"    ChannelName  : {gi.get('ChannelName')}")
        print(f"    ReferenceNum : {gi.get('ReferenceNum')}")
        print(f"    ExternalRef  : {gi.get('ExternalRef')}")
        print(f"    SecondaryRef : {gi.get('SecondaryRef')}")
        print(f"    ReceivedDate : {gi.get('ReceivedDate', '')[:10]}")
        print(f"    Tracking     : {si.get('TrackingNumber')}")
        print(f"    PostalSvc    : {si.get('PostalServiceName')}")

    # Save full raw dump
    json.dump({"total": total, "data": entries},
              open("logs/lw_amazon_orders_dump.json", "w"), indent=2)
    print(f"\n  Full dump → logs/lw_amazon_orders_dump.json")


def main():
    # The Amazon order ID we're trying to find
    TEST_ORDER_ID = "204-7360956-1404360"

    print("=" * 55)
    print("  debug_lw.py — Linnworks order search diagnostics")
    print("=" * 55)

    lw_token, lw_server = auth_linnworks()
    if not lw_token:
        return

    # ── Strategy 1: Source=Amazon + ReferenceNum=<id> (current approach) ──
    search_orders(lw_token, lw_server, {
        "TextFields": [
            {"FieldCode": "GENERAL_INFO_SOURCE",        "Type": 0, "Text": "Amazon"},
            {"FieldCode": "GENERAL_INFO_REFERENCE_NUM", "Type": 0, "Text": TEST_ORDER_ID},
        ]
    }, label=f"Source=Amazon + ReferenceNum={TEST_ORDER_ID}")

    # ── Strategy 2: ReferenceNum only ──
    search_orders(lw_token, lw_server, {
        "TextFields": [
            {"FieldCode": "GENERAL_INFO_REFERENCE_NUM", "Type": 0, "Text": TEST_ORDER_ID},
        ]
    }, label=f"ReferenceNum={TEST_ORDER_ID} (no source filter)")

    # ── Strategy 3: ExternalRef ──
    search_orders(lw_token, lw_server, {
        "TextFields": [
            {"FieldCode": "GENERAL_INFO_EXTERNAL_REF", "Type": 0, "Text": TEST_ORDER_ID},
        ]
    }, label=f"ExternalRef={TEST_ORDER_ID}")

    # ── Strategy 4: Contains search on ReferenceNum ──
    search_orders(lw_token, lw_server, {
        "TextFields": [
            {"FieldCode": "GENERAL_INFO_REFERENCE_NUM", "Type": 2, "Text": TEST_ORDER_ID},
        ]
    }, label=f"ReferenceNum contains {TEST_ORDER_ID} (Type=2)")

    # ── Strategy 5: Source=Amazon only (no order ID filter) ──
    search_orders(lw_token, lw_server, {
        "TextFields": [
            {"FieldCode": "GENERAL_INFO_SOURCE", "Type": 0, "Text": "Amazon"},
        ]
    }, label="Source=Amazon only (what's the source value?)")

    # ── Dump recent Amazon orders to see real field values ──
    dump_recent_amazon_orders(lw_token, lw_server)


if __name__ == "__main__":
    main()
