# Deploying to Azure Container Apps

This guide deploys VOC Copilot to Azure with [`deploy/azure/main.bicep`](../../deploy/azure/main.bicep): the API, the ingestion worker and the web UI on Azure Container Apps, with sign-in through Microsoft Entra ID. The design choices are in [ADR 0014](../adr/0014-azure-container-apps.md).

```text
Internet ──► oauth2-proxy (sign-in) ──► web (nginx + UI) ──► api ──► PostgreSQL 16 + pgvector
                                                              ▲          ▲
         machine clients (X-API-Key, optional) ───────────────┘  worker ─┴──► Azure Managed Redis
```

| Resource | What for | Starting size |
|---|---|---|
| Container Apps environment + Log Analytics | Runs the apps; collects their logs | Consumption profile |
| `voc-proxy` (oauth2-proxy) | The only public entry for people; signs them in | 0.25 vCPU, 1–2 replicas |
| `voc-web` (nginx + React build) | The UI; internal ingress in SSO mode | 0.25 vCPU, 1–2 replicas |
| `voc-api` (FastAPI) | `/v1/*`; public only for machine clients (`exposeApi`) | 0.5 vCPU, 1–4 replicas |
| `voc-worker` (arq) | Chunking, embeddings, extraction | 0.5 vCPU, 1 replica |
| `voc-migrate` (Container Apps job) | `alembic upgrade head`, started by hand | runs on demand |
| PostgreSQL Flexible Server 16 | Data, vectors (pgvector), row-level security | Burstable B1ms, 32 GB |
| Azure Managed Redis | Job queue, rate-limit counters | Balanced B0 |

Costs depend on region and usage; put these sizes into the [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/). The always-on replicas, the database and Redis make up most of the baseline. LLM usage is billed by your provider, and the app reports it per request (`usage.cost_usd`).

## Prerequisites

- An Azure subscription where you can create resource groups.
- Permission to create app registrations in Microsoft Entra ID, for sign-in.
- The Azure CLI with Bicep (`az bicep install`), logged in with `az login`.
- An LLM: an OpenAI key, or an Azure OpenAI resource with `gpt-4o-mini` and `text-embedding-3-small` deployments.

## 1. Publish the images

Push a version tag. [`release.yml`](../../.github/workflows/release.yml) builds both images and pushes them to GitHub Container Registry:

```bash
git tag v0.1.0 && git push origin v0.1.0
# → ghcr.io/<owner>/voc-copilot-api:0.1.0 and ghcr.io/<owner>/voc-copilot-web:0.1.0
```

Packages of a private repository are private. Either make the two packages public (package settings on GitHub) or create a token with only `read:packages` for Azure to pull with.

## 2. Register the app in Entra ID

```bash
APP_ID=$(az ad app create --display-name "VOC Copilot" --sign-in-audience AzureADMyOrg \
  --query appId -o tsv)
az ad sp create --id "$APP_ID"
CLIENT_SECRET=$(az ad app credential reset --id "$APP_ID" --display-name oauth2-proxy \
  --years 1 --query password -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
```

The redirect URI is only known after the deployment creates the proxy's address; step 5 adds it.

Entra ID puts the sign-in name in `preferred_username`, which is why the example parameters set `oidcEmailClaim = 'preferred_username'`. To use the `email` claim instead, add it as an optional claim of the ID token (app registration → Token configuration) and set `oidcEmailClaim = 'email'`.

## 3. Generate secrets

Generate them in your shell; they reach Azure only as secure deployment parameters, stored as Container Apps secrets.

```bash
export VOC_API_IMAGE=ghcr.io/<owner>/voc-copilot-api:0.1.0
export VOC_WEB_IMAGE=ghcr.io/<owner>/voc-copilot-web:0.1.0
export VOC_REGISTRY_USERNAME=<github-user>            # empty if the packages are public
export VOC_REGISTRY_PASSWORD=<read:packages token>    # empty if the packages are public

export VOC_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
export VOC_POSTGRES_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
export VOC_COOKIE_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32)[:32])")
export VOC_LLM_API_KEY=<your OpenAI key>

export VOC_OIDC_ISSUER="https://login.microsoftonline.com/$TENANT_ID/v2.0"
export VOC_OIDC_CLIENT_ID=$APP_ID
export VOC_OIDC_CLIENT_SECRET=$CLIENT_SECRET
```

Keep `VOC_API_KEY` in a password manager: it is the default tenant's admin key, and you use it in step 6.

## 4. Deploy

```bash
az group create --name voc-copilot --location westeurope
az deployment group create --resource-group voc-copilot \
  --template-file deploy/azure/main.bicep --parameters deploy/azure/main.bicepparam \
  --query properties.outputs
```

To use Azure OpenAI, set `llmProvider = 'azure_openai'`, `azureOpenAIEndpoint` and `azureOpenAIApiVersion` in the parameters file, and use the Azure OpenAI key as `VOC_LLM_API_KEY`. To try it without sign-in, set `authMode = 'apiKey'`; the web app is then public and acts as the default tenant's admin, so use this for demos only.

The outputs are `appUrl`, `redirectUri`, `apiUrl` and the names used below. The API and the worker report not-ready until the schema exists, which is the next step.

## 5. Create the schema and finish sign-in

```bash
az containerapp job start --resource-group voc-copilot --name voc-migrate
az containerapp job execution list --resource-group voc-copilot --name voc-migrate \
  --query "[0].properties.status" -o tsv          # wait for Succeeded

az ad app update --id "$APP_ID" --web-redirect-uris "<redirectUri output>"
```

Migration `0001` creates the `vector` extension, which the template allow-lists (`azure.extensions = VECTOR`). Migrations run as the server administrator. Azure's administrator is not a Postgres superuser, so row-level security applies to it too. The app still switches to `voc_app` for every tenant transaction ([ADR 0012](../adr/0012-tenants-api-keys-and-row-level-security.md)).

## 6. Give people access

Signing in proves who someone is. Access comes from VOC Copilot's user list ([ADR 0013](../adr/0013-user-sign-in-with-oidc.md)). Add yourself as admin of the default tenant with the bootstrap key:

```bash
API=<apiUrl output>
curl -sS -X POST "$API/v1/users" -H "X-API-Key: $VOC_API_KEY" -H "Content-Type: application/json" \
  -d '{"email": "you@yourcompany.com", "role": "admin"}'
```

Open `appUrl` and sign in. From there, admins add colleagues through `/v1/users` and machine keys through `/v1/api-keys`. To create more tenants, run the operator CLI inside the API container:

```bash
az containerapp exec --resource-group voc-copilot --name voc-api \
  --command "python -m scripts.tenants create acme --name 'Acme Telecom'"
az containerapp exec --resource-group voc-copilot --name voc-api \
  --command "python -m scripts.tenants add-user acme ana@acme.com --role admin"
```

## 7. Check it

```bash
curl -sS "$API/ready"                                   # {"status":"ok",...}
curl -sS "$API/v1/me" -H "X-API-Key: $VOC_API_KEY"      # default tenant, admin
```

Then upload conversations with an `ingest` key ([`scripts/ingest.py`](../../scripts/ingest.py) or `POST /v1/conversations`), and watch the job at `/v1/jobs/{id}`.

Logs are in the Log Analytics workspace:

```kusto
ContainerAppConsoleLogs_CL
| where ContainerAppName_s in ("voc-api", "voc-worker")
| project TimeGenerated, ContainerAppName_s, Log_s
| order by TimeGenerated desc
```

For traces, set `otlpEndpoint` to an OpenTelemetry collector, or to Langfuse or Grafana Tempo ([ADR 0008](../adr/0008-opentelemetry-tracing-and-cost.md)).

## Updating

Migrate first, then roll the apps. Each migration is written to work with the previous image, and the reverse order would run new code on an old schema.

```bash
NEW=0.2.0
az containerapp job update -g voc-copilot -n voc-migrate --image ghcr.io/<owner>/voc-copilot-api:$NEW
az containerapp job start  -g voc-copilot -n voc-migrate      # wait for Succeeded
for app in voc-api voc-worker; do
  az containerapp update -g voc-copilot -n $app --image ghcr.io/<owner>/voc-copilot-api:$NEW
done
az containerapp update -g voc-copilot -n voc-web --image ghcr.io/<owner>/voc-copilot-web:$NEW
```

After that, update the image tags in your parameters, so the next `az deployment group create` doesn't roll them back.

## Hardening for production

The template is a secure baseline:
- TLS everywhere;
- secrets as Container Apps secrets;
- the UI reachable only through sign-in;
- the database reachable only from Azure.

For production, add these layers:

- **Private network.** Deploy the Container Apps environment into a VNet. Give PostgreSQL and Redis private endpoints and turn off their public access. With this, the "Allow Azure services" firewall rule goes away. That rule lets in any Azure customer's traffic, though they still need the password.
- **Key Vault and managed identity.** Store the secrets in Key Vault and reference them from Container Apps through a user-assigned identity. Use Entra authentication for PostgreSQL and Redis instead of passwords and keys.
- **Least-privilege logins** ([ADR 0007](../adr/0007-guarded-text-to-sql.md), [ADR 0012](../adr/0012-tenants-api-keys-and-row-level-security.md)):
  - The app and the worker log in as a role that is only a member of `voc_app`.
  - Text-to-SQL logs in as a role that is only a member of `voc_analytics`, set through `ANALYTICS_DATABASE_URL`.
  - Only the migration job uses the administrator.

  For example (run with psql as the administrator):

  ```sql
  CREATE ROLE voc_app_login LOGIN PASSWORD '<random>' IN ROLE voc_app;
  CREATE ROLE voc_analytics_login LOGIN PASSWORD '<random>' IN ROLE voc_analytics;
  GRANT SELECT ON tenants, api_keys, users TO voc_app_login;  -- authentication lookups
  ```
- **Availability.** Use zone-redundant high availability on PostgreSQL, a General Purpose SKU, geo-redundant backups, and two or more API replicas.
- **Edge.** Add a custom domain with a managed certificate (`az containerapp hostname`). Put Azure Front Door with WAF in front of the proxy and the API.
- **Operations.** Set alerts on 5xx rates, job failures (`processing_error`), Postgres CPU and storage, and Redis memory. Rotate `VOC_API_KEY` and the client secret on a schedule.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Migration job fails: `extension "vector" is not allow-listed` | The `azure.extensions` server parameter didn't apply. Set it to `VECTOR` and run the job again. |
| `/ready` reports the database down | Check the firewall rule, `?ssl=require`, and that the password was URL-encoded (the template does this). |
| `/ready` reports Redis down | Azure Managed Redis listens on port **10000** with TLS (`rediss://`). |
| "You don't have access yet" after sign-in | The signed-in email isn't on the user list (step 6), or `oidcEmailClaim` names a claim the token doesn't have. |
| oauth2-proxy: redirect URI mismatch | The app registration's redirect URI must equal the `redirectUri` output exactly. |
| UI loads but every API call returns 404 | nginx must not forward the browser's `Host` to the API: Container Apps routes internal traffic by `Host` (fixed in `web/nginx.conf.template`). |

## Tearing down

```bash
az group delete --name voc-copilot
az ad app delete --id "$APP_ID"
```
