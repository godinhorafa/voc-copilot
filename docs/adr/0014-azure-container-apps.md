# 0014. Deploy on Azure Container Apps with Bicep

- **Status:** Accepted
- **Date:** 2026-11-18

## Context

VOC Copilot runs as four containers: the API, the worker, the web UI and, with SSO, oauth2-proxy. It needs PostgreSQL with pgvector and Redis. The target audience works on Azure. The deployment should be reproducible from the repository, cheap at low traffic, and secure by default. A team should be able to run it without a platform team.

## Decision

- **Azure Container Apps** for the four containers, plus a job for migrations, in one Consumption environment.
  - **Traffic:** internal apps reach each other by name. Ingress terminates TLS.
  - **Scaling:** HTTP scaling for the API. One worker replica, because ingestion is queue-bound.
  - **Logs:** Log Analytics.
- **Exposure.**
  - **SSO mode:** only oauth2-proxy is public. The web app has internal ingress, so the UI is unreachable without signing in.
  - **API:** public only when machine clients need it (`exposeApi`); otherwise internal.
- **Data.**
  - **PostgreSQL Flexible Server 16:** pgvector allow-listed through `azure.extensions`; connections over TLS.
  - **Azure Managed Redis:** TLS on port 10000, the `EnterpriseCluster` policy so non-cluster clients work, and no eviction, so queued jobs are never dropped.
- **Infrastructure as code in Bicep** (`deploy/azure/main.bicep`).
  - **Parameters:** a parameters file that reads every secret from environment variables, so no secret is ever committed.
  - **CI:** lints and compiles both files, so a broken template fails the pull request.
- **Images on GitHub Container Registry**, built by `release.yml` on version tags.
- **Migrations are a manual job, started before the apps are rolled.** Migrations are written so the previous image keeps working, which makes "migrate, then roll" safe.

## Alternatives considered

- **AKS.** Full control and the richest ecosystem, but it needs cluster operations, node pools and an ingress controller. That is too much for four stateless containers.
- **App Service for Containers.** It is simpler for one web app. Background workers, internal-only apps and app-to-app discovery are clumsier, and scaling to zero isn't available.
- **Redis as a container app.** Cheaper, but it needs TCP ingress, which requires a VNet-integrated environment, and it adds persistence concerns. Azure Cache for Redis is being retired in favor of Azure Managed Redis.
- **Terraform.** It is a fine choice, especially across clouds. Bicep needs no state backend, ships with the Azure CLI, and the compiler validates resource properties against Azure's schemas. Here that was the only validation possible without a subscription.
- **Running migrations at API start-up.** It is convenient, but every replica races to migrate, and a failed migration takes the API down with it.
- **Private networking by default.** It is the right production posture, but the VNet, private DNS zones and private endpoints roughly double the template and add cost. The guide lists it first under hardening, and the baseline only lets in Azure-originated connections over TLS with strong passwords.

## Consequences

- **What was verified.** The template compiles and lints with no warnings against the Azure resource schemas. It has not been deployed from this repository's CI, because that would need a subscription. The first real deployment should follow the guide step by step.
- **Two-phase sign-in setup.** The app registration's redirect URI uses the proxy's address, which is only known after the first deployment.
- **Default database access.** The database administrator is used by the apps until the least-privilege logins from the hardening section are set up. Row-level security still applies, because Azure's administrator is not a superuser.
- **Baseline cost.** One always-on replica per app plus the database and Redis. Scale-to-zero is possible for the web and proxy apps at the price of cold starts.
