"""
upload_settings.py — Upload .env credentials to Azure Function App
Fetches existing Azure settings first, merges credentials in,
then PUTs everything back — no system settings are lost.
"""
import os, json, subprocess, sys
from dotenv import load_dotenv

load_dotenv(dotenv_path="../.env")

AZ             = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
SUBSCRIPTION   = "9e81154b-6d55-4430-8598-10af25a2a2c9"
RESOURCE_GROUP = "rg-amz-lw-sync"
APP_NAME       = "amz-lw-tracking"
BASE_URL       = (f"https://management.azure.com/subscriptions/{SUBSCRIPTION}"
                  f"/resourceGroups/{RESOURCE_GROUP}/providers/Microsoft.Web"
                  f"/sites/{APP_NAME}")

# ── Step 1: Fetch existing settings ───────────────────────────
print("Fetching current Azure settings...")
r = subprocess.run(
    [AZ, "rest", "--method", "GET",
     "--url", f"{BASE_URL}/config/appsettings?api-version=2022-03-01"],
    capture_output=True, text=True
)
if r.returncode != 0:
    print(f"Failed to fetch: {r.stderr[:300]}")
    sys.exit(1)

existing = json.loads(r.stdout).get("properties", {})
print(f"  Found {len(existing)} existing settings")

# ── Step 2: Merge — preserve system settings, add ours ────────
our_settings = {
    "ASL_LWA_APP_ID":        os.getenv("ASL_LWA_APP_ID", ""),
    "ASL_LWA_CLIENT_SECRET": os.getenv("ASL_LWA_CLIENT_SECRET", ""),
    "ASL_LWA_REFRESH_TOKEN": os.getenv("ASL_LWA_REFRESH_TOKEN", ""),
    "LINNWORKS_APP_ID":      os.getenv("LINNWORKS_APP_ID", ""),
    "LINNWORKS_APP_SECRET":  os.getenv("LINNWORKS_APP_SECRET", ""),
    "LINNWORKS_TOKEN":       os.getenv("LINNWORKS_TOKEN", ""),
}

merged = {**existing, **our_settings}

print("\nCredentials to add/update:")
for k, v in our_settings.items():
    masked = v[:6] + "..." + v[-4:] if len(v) > 12 else "***"
    print(f"  {k}: {masked}")

# ── Step 3: PUT merged settings back ──────────────────────────
body = {"properties": merged}
tmp  = os.path.join(os.environ.get("TEMP", "."), "az_body.json")
with open(tmp, "w") as f:
    json.dump(body, f)

print(f"\nUploading {len(merged)} total settings...")
result = subprocess.run(
    [AZ, "rest", "--method", "PUT",
     "--url", f"{BASE_URL}/config/appsettings?api-version=2022-03-01",
     "--body", f"@{tmp}"],
    capture_output=True, text=True
)

if result.returncode == 0:
    saved_keys = list(json.loads(result.stdout).get("properties", {}).keys())
    our_saved  = [k for k in saved_keys if k in our_settings]
    sys_saved  = [k for k in saved_keys if k not in our_settings]
    print(f"SUCCESS!")
    print(f"  Credentials uploaded : {len(our_saved)}/6")
    print(f"  System settings kept : {len(sys_saved)}")
    for k in our_saved:
        print(f"    + {k}")
else:
    print(f"Failed: {result.stderr[:500]}")
    sys.exit(1)
