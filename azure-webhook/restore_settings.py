"""
restore_settings.py — Restore ALL required Azure Function App settings
including system settings + your credentials.
"""
import os, json, subprocess, sys
from dotenv import load_dotenv

load_dotenv(dotenv_path="../.env")

AZ             = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
SUBSCRIPTION   = "9e81154b-6d55-4430-8598-10af25a2a2c9"
RESOURCE_GROUP = "rg-amz-lw-sync"
APP_NAME       = "amz-lw-tracking"
STORAGE_NAME   = "amzlwsync7341"
BASE_URL       = (f"https://management.azure.com/subscriptions/{SUBSCRIPTION}"
                  f"/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.Web"
                  f"/sites/{APP_NAME}")

# ── Get storage connection string ──────────────────────────────
print("Getting storage connection string...")
r = subprocess.run(
    [AZ, "storage", "account", "show-connection-string",
     "--name", STORAGE_NAME,
     "--resource-group", RESOURCE_GROUP,
     "--query", "connectionString", "-o", "tsv"],
    capture_output=True, text=True
)
storage_conn = r.stdout.strip()
if not storage_conn:
    print(f"ERROR getting storage: {r.stderr[:300]}")
    sys.exit(1)
print(f"  Storage: DefaultEndpointsProtocol=https;AccountName={STORAGE_NAME}...")

# ── Get App Insights connection string (optional, skip if slow) ─
ai_conn = ""
print("Skipping App Insights (optional)...")

# ── Build complete settings ────────────────────────────────────
settings = {
    # Azure Functions required system settings
    "FUNCTIONS_WORKER_RUNTIME":    "python",
    "FUNCTIONS_EXTENSION_VERSION": "~4",
    "AzureWebJobsStorage":         storage_conn,
    # NOTE: do NOT set WEBSITE_RUN_FROM_PACKAGE for Linux Consumption
    # — it conflicts with the squashfs deployment method

    # Your credentials
    "ASL_LWA_APP_ID":        os.getenv("ASL_LWA_APP_ID", ""),
    "ASL_LWA_CLIENT_SECRET": os.getenv("ASL_LWA_CLIENT_SECRET", ""),
    "ASL_LWA_REFRESH_TOKEN": os.getenv("ASL_LWA_REFRESH_TOKEN", ""),
    "LINNWORKS_APP_ID":      os.getenv("LINNWORKS_APP_ID", ""),
    "LINNWORKS_APP_SECRET":  os.getenv("LINNWORKS_APP_SECRET", ""),
    "LINNWORKS_TOKEN":       os.getenv("LINNWORKS_TOKEN", ""),
}

if ai_conn:
    settings["APPLICATIONINSIGHTS_CONNECTION_STRING"] = ai_conn

# ── PUT all settings ───────────────────────────────────────────
body = {"properties": settings}
tmp  = os.path.join(os.environ.get("TEMP", "."), "az_restore.json")
with open(tmp, "w") as f:
    json.dump(body, f)

print(f"\nRestoring {len(settings)} settings to Azure Function App...")
result = subprocess.run(
    [AZ, "rest", "--method", "PUT",
     "--url", f"{BASE_URL}/config/appsettings?api-version=2022-03-01",
     "--body", f"@{tmp}"],
    capture_output=True, text=True
)

if result.returncode == 0:
    saved = list(json.loads(result.stdout).get("properties", {}).keys())
    print(f"SUCCESS! {len(saved)} settings saved:")
    for k in saved:
        print(f"  + {k}")
    print("\nWaiting 15s for app to restart...")
    import time; time.sleep(15)

    # Test the URL
    import urllib.request
    try:
        resp = urllib.request.urlopen(
            "https://amz-lw-tracking.azurewebsites.net/api/amz-tracking",
            timeout=20
        )
        print(f"\nWebhook test: {resp.status}")
        print(resp.read().decode())
    except Exception as e:
        print(f"\nWebhook test failed: {e}")
        print("App may still be restarting — try again in 30s")
else:
    print(f"FAILED: {result.stderr[:500]}")
    sys.exit(1)
