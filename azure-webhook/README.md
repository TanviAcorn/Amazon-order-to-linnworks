# Amazon → Linnworks Tracking Webhook

Automatically syncs tracking numbers from Amazon to Linnworks the moment a label is printed.

## How it works

```
Print label on shipping.amazon.co.uk
          ↓
Amazon fires ORDER_CHANGE notification
          ↓
Azure Function receives it (HTTPS webhook)
          ↓
Fetches tracking from Amazon SP-API
          ↓
Finds order in Linnworks (all fulfilment centres)
          ↓
Writes tracking number → order ready for pickwave
```

## Cost

Azure Functions Consumption plan:
- **1 million executions free per month**
- At ~200 orders/day = ~6,000 executions/month
- **Cost: £0/month**

---

## Setup (one time)

### Prerequisites

1. **Azure CLI**
   ```
   winget install Microsoft.Azure.CLI
   ```

2. **Azure Functions Core Tools**
   ```
   winget install Microsoft.Azure.FunctionsCoreTools
   ```

3. **Log in to Azure**
   ```
   az login
   ```

### Deploy

```powershell
cd "D:\Amazon order to linnworks\azure-webhook"
.\deploy.ps1
```

The script will:
- Create a resource group `rg-amz-lw-sync` in UK South
- Create a storage account and Function App (Consumption plan)
- Upload your credentials from `.env`
- Deploy the function code
- Print your webhook URL

### Subscribe Amazon to your webhook

After deploy, run once:
```
python setup_subscription.py --webhook-url https://<your-app>.azurewebsites.net/api/amz-tracking
```

This tells Amazon to POST to your Azure Function every time an order changes status to Shipped.

---

## Files

| File | Purpose |
|------|---------|
| `function_app.py` | The webhook handler — receives SNS, fetches tracking, updates Linnworks |
| `setup_subscription.py` | One-time: registers the Amazon notification subscription |
| `deploy.ps1` | One-time: creates Azure resources and deploys code |
| `requirements.txt` | Python dependencies |
| `host.json` | Azure Functions runtime config |

---

## Monitoring

View live logs in Azure Portal:
- Portal → Function Apps → `amz-lw-tracking-XXXX` → Functions → `amz-tracking` → Monitor

Or via CLI:
```
az functionapp logs tail --name <your-app-name> --resource-group rg-amz-lw-sync
```

---

## Manual fallback

If the webhook ever misses an order, the original scripts still work:
```
python ..\fetch_orders.py       # get yesterday's orders
python ..\push_tracking.py      # push a specific one
```
