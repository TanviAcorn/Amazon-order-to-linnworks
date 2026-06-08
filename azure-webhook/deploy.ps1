# deploy.ps1 — Deploy Amazon→Linnworks webhook to Azure Functions
# ─────────────────────────────────────────────────────────────────
# Prerequisites:
#   1. Azure CLI installed  →  https://learn.microsoft.com/en-us/cli/azure/install-azure-cli
#   2. Azure Functions Core Tools  →  winget install Microsoft.Azure.FunctionsCoreTools
#   3. Run:  az login
#
# Usage:  .\deploy.ps1
# ─────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

# ── CONFIGURATION — change these if you want ──────────────────
$RESOURCE_GROUP    = "rg-amz-lw-sync"
$LOCATION          = "uksouth"               # closest Azure region to you (UK)
$STORAGE_ACCOUNT   = "amzlwsync$(Get-Random -Maximum 9999)"   # must be globally unique
$FUNCTION_APP_NAME = "amz-lw-tracking-$(Get-Random -Maximum 9999)"
$PYTHON_VERSION    = "3.11"
# ──────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Deploying Amazon → Linnworks tracking webhook to Azure" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# ── 1. Check Azure CLI is logged in ───────────────────────────
Write-Host "`n[1/6] Checking Azure login..." -ForegroundColor Yellow
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "  Not logged in. Running az login..." -ForegroundColor Red
    az login
    $account = az account show | ConvertFrom-Json
}
Write-Host "  ✅ Logged in as: $($account.user.name)  |  Subscription: $($account.name)"

# ── 2. Create resource group ───────────────────────────────────
Write-Host "`n[2/6] Creating resource group: $RESOURCE_GROUP in $LOCATION..." -ForegroundColor Yellow
az group create --name $RESOURCE_GROUP --location $LOCATION --output none
Write-Host "  ✅ Resource group ready"

# ── 3. Create storage account (required by Azure Functions) ───
Write-Host "`n[3/6] Creating storage account: $STORAGE_ACCOUNT..." -ForegroundColor Yellow
az storage account create `
    --name $STORAGE_ACCOUNT `
    --location $LOCATION `
    --resource-group $RESOURCE_GROUP `
    --sku Standard_LRS `
    --output none
Write-Host "  ✅ Storage account ready"

# ── 4. Create Function App ─────────────────────────────────────
Write-Host "`n[4/6] Creating Function App: $FUNCTION_APP_NAME..." -ForegroundColor Yellow
az functionapp create `
    --name $FUNCTION_APP_NAME `
    --resource-group $RESOURCE_GROUP `
    --storage-account $STORAGE_ACCOUNT `
    --consumption-plan-location $LOCATION `
    --runtime python `
    --runtime-version $PYTHON_VERSION `
    --functions-version 4 `
    --os-type Linux `
    --output none
Write-Host "  ✅ Function App created"

# ── 5. Set environment variables (app settings) ───────────────
Write-Host "`n[5/6] Setting credentials as app settings..." -ForegroundColor Yellow

# Load from .env file
$envFile = Join-Path $PSScriptRoot "../.env"
$envVars = @{}
foreach ($line in Get-Content $envFile) {
    $line = $line.Trim()
    if ($line -match "^([^#=]+)\s*=\s*(.+)$") {
        $envVars[$matches[1].Trim()] = $matches[2].Trim()
    }
}

$settings = @(
    "ASL_LWA_APP_ID=$($envVars['ASL_LWA_APP_ID'])"
    "ASL_LWA_CLIENT_SECRET=$($envVars['ASL_LWA_CLIENT_SECRET'])"
    "ASL_LWA_REFRESH_TOKEN=$($envVars['ASL_LWA_REFRESH_TOKEN'])"
    "LINNWORKS_APP_ID=$($envVars['LINNWORKS_APP_ID'])"
    "LINNWORKS_APP_SECRET=$($envVars['LINNWORKS_APP_SECRET'])"
    "LINNWORKS_TOKEN=$($envVars['LINNWORKS_TOKEN'])"
)

az functionapp config appsettings set `
    --name $FUNCTION_APP_NAME `
    --resource-group $RESOURCE_GROUP `
    --settings @settings `
    --output none

Write-Host "  ✅ Credentials configured"

# ── 6. Deploy function code ────────────────────────────────────
Write-Host "`n[6/6] Deploying function code..." -ForegroundColor Yellow
func azure functionapp publish $FUNCTION_APP_NAME --python

# ── Done ───────────────────────────────────────────────────────
$webhookUrl = "https://$FUNCTION_APP_NAME.azurewebsites.net/api/amz-tracking"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  ✅ DEPLOYMENT COMPLETE" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Webhook URL: $webhookUrl" -ForegroundColor White
Write-Host ""
Write-Host "  Next step — run this to subscribe Amazon to your webhook:" -ForegroundColor Yellow
Write-Host "  python setup_subscription.py --webhook-url $webhookUrl" -ForegroundColor Cyan
Write-Host ""
Write-Host "  That's it. Every time a label is printed on Amazon Shipping," -ForegroundColor White
Write-Host "  the tracking will automatically appear in Linnworks." -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Green
