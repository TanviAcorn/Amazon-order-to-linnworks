"""
setup_subscription.py — One-time setup: subscribe to Amazon ORDER_CHANGE notifications
────────────────────────────────────────────────────────────────────────────────────────
Run this ONCE after deploying the Azure Function to register your webhook URL
with Amazon's Notifications API.

Amazon will POST ORDER_CHANGE events to your Azure Function URL every time
an order is shipped (tracking assigned).

Usage:
    python setup_subscription.py --webhook-url https://<your-app>.azurewebsites.net/api/amz-tracking

Requirements:
    pip install requests python-dotenv
"""

import os, sys, json, argparse, requests
from dotenv import load_dotenv

load_dotenv(dotenv_path="../.env")

AMZ_APP_ID        = os.getenv("ASL_LWA_APP_ID")
AMZ_CLIENT_SECRET = os.getenv("ASL_LWA_CLIENT_SECRET")
AMZ_REFRESH_TOKEN = os.getenv("ASL_LWA_REFRESH_TOKEN")
MARKETPLACE_ID    = "A1F83G8C2ARO7P"

NOTIFICATIONS_URL = "https://sellingpartnerapi-eu.amazon.com/notifications/v1"


def auth_amazon():
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
    token = d.get("access_token")
    if token:
        print("  ✅ Amazon token OK")
        return token
    print(f"  ❌ Amazon auth failed: {d}")
    return None


def get_existing_subscriptions(amz_token):
    """List current ORDER_CHANGE subscriptions."""
    r = requests.get(
        f"{NOTIFICATIONS_URL}/subscriptions/ORDER_CHANGE",
        headers={"x-amz-access-token": amz_token},
        timeout=30,
    )
    print(f"\n  Existing subscriptions: {r.status_code}")
    if r.status_code == 200:
        subs = r.json().get("payload", {}).get("subscriptions", [])
        for s in subs:
            print(f"    ID          : {s.get('subscriptionId')}")
            print(f"    Destination : {s.get('destinationId')}")
            print(f"    Status      : {s.get('processingDirective', {}).get('eventFilter', {})}")
    else:
        print(f"    {r.text[:300]}")
    return r


def create_destination(amz_token, webhook_url):
    """
    Register your Azure Function URL as an SNS-HTTPS destination.
    Amazon will POST notifications directly to this URL.
    """
    print(f"\n  Creating destination for: {webhook_url}")

    r = requests.post(
        f"{NOTIFICATIONS_URL}/destinations",
        headers={
            "x-amz-access-token": amz_token,
            "Content-Type":       "application/json",
        },
        json={
            "resourceSpecification": {
                "sqs": None,
                "eventBridge": None,
                "https": {
                    "url": webhook_url,
                }
            },
            "name": "AzureFunction_LinnworksSync",
        },
        timeout=30,
    )

    print(f"  Status: {r.status_code}")
    try:
        data = r.json()
        print(f"  Response: {json.dumps(data, indent=2)}")
    except Exception:
        print(f"  Response: {r.text[:300]}")

    if r.status_code in (200, 201):
        destination_id = r.json().get("payload", {}).get("destinationId")
        print(f"  ✅ Destination ID: {destination_id}")
        return destination_id

    print("  ❌ Failed to create destination")
    return None


def create_subscription(amz_token, destination_id):
    """Subscribe to ORDER_CHANGE notifications for the destination."""
    print(f"\n  Creating ORDER_CHANGE subscription...")

    r = requests.post(
        f"{NOTIFICATIONS_URL}/subscriptions/ORDER_CHANGE",
        headers={
            "x-amz-access-token": amz_token,
            "Content-Type":       "application/json",
        },
        json={
            "payloadVersion": "2.0",
            "destinationId":  destination_id,
            "processingDirective": {
                "eventFilter": {
                    "eventFilterType": "ORDER_CHANGE",
                    "orderChangeTypes": ["OrderStatusChange"],  # fires when shipped
                }
            },
        },
        timeout=30,
    )

    print(f"  Status: {r.status_code}")
    try:
        data = r.json()
        print(f"  Response: {json.dumps(data, indent=2)}")
    except Exception:
        print(f"  Response: {r.text[:300]}")

    if r.status_code in (200, 201):
        sub_id = r.json().get("payload", {}).get("subscriptionId")
        print(f"  ✅ Subscription ID: {sub_id}")
        print(f"     Save this — you need it to delete/update the subscription later.")
        return sub_id

    print("  ❌ Failed to create subscription")
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--webhook-url", required=True,
        help="Your Azure Function URL, e.g. https://myapp.azurewebsites.net/api/amz-tracking"
    )
    parser.add_argument(
        "--list-only", action="store_true",
        help="Just list existing subscriptions, don't create new ones"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Amazon Notifications — Subscription Setup")
    print("=" * 60)

    amz_token = auth_amazon()
    if not amz_token:
        sys.exit(1)

    get_existing_subscriptions(amz_token)

    if args.list_only:
        return

    destination_id = create_destination(amz_token, args.webhook_url)
    if not destination_id:
        sys.exit(1)

    sub_id = create_subscription(amz_token, destination_id)
    if not sub_id:
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  ✅ Setup complete!")
    print(f"  Webhook URL    : {args.webhook_url}")
    print(f"  Destination ID : {destination_id}")
    print(f"  Subscription ID: {sub_id}")
    print()
    print("  Amazon will now POST to your Azure Function every time")
    print("  an order status changes to Shipped.")
    print("=" * 60)


if __name__ == "__main__":
    main()
