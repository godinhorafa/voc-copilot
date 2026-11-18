// VOC Copilot on Azure Container Apps. Guide: docs/deploy/azure-container-apps.md
//
//   Internet ──► oauth2-proxy (sign-in) ──► web (nginx + UI) ──► api ──► PostgreSQL + pgvector
//                                                                 ▲         ▲
//            machine clients (X-API-Key, optional) ───────────────┘   worker ┘──► Azure Managed Redis
//
// authMode 'sso': only oauth2-proxy is public; people sign in with your OpenID
// provider and the API verifies their ID token (ADR 0013).
// authMode 'apiKey': the web app is public and its nginx adds API_KEY to every
// call, so anyone who can open it is the default tenant's admin. Demos only.

targetScope = 'resourceGroup'

@description('Short prefix for resource names (lowercase letters and digits).')
@minLength(3)
@maxLength(12)
param prefix string = 'voc'

param location string = resourceGroup().location

@description('API image, e.g. ghcr.io/<owner>/voc-copilot-api:<tag> (see .github/workflows/release.yml).')
param apiImage string

@description('Web UI image, e.g. ghcr.io/<owner>/voc-copilot-web:<tag>.')
param webImage string

param oauth2ProxyImage string = 'quay.io/oauth2-proxy/oauth2-proxy:v7.12.0'

@description('Registry the images are pulled from; leave the credentials empty for public images.')
param registryServer string = 'ghcr.io'
param registryUsername string = ''
@secure()
param registryPassword string = ''

@allowed(['sso', 'apiKey'])
param authMode string = 'sso'

@description('Also expose the API publicly for machine clients (X-API-Key), e.g. CRM uploads.')
param exposeApi bool = true

@description('Bootstrap admin key of the default tenant (API_KEY). Use a long random value.')
@secure()
@minLength(32)
param apiKey string

@description('PostgreSQL administrator login.')
param postgresAdminLogin string = 'vocadmin'
@secure()
@minLength(16)
param postgresAdminPassword string

@allowed(['openai', 'azure_openai', 'ollama', 'fake'])
param llmProvider string = 'openai'
param llmModel string = 'gpt-4o-mini'
param embeddingModel string = 'text-embedding-3-small'
@description('OPENAI_API_KEY or AZURE_OPENAI_API_KEY, depending on llmProvider.')
@secure()
param llmApiKey string = ''
param azureOpenAIEndpoint string = ''
param azureOpenAIApiVersion string = ''

@description('OpenID Connect issuer, e.g. https://login.microsoftonline.com/<tenant-id>/v2.0 (sso mode).')
param oidcIssuer string = ''
@description('Client (application) id registered with the provider (sso mode).')
param oidcClientId string = ''
@secure()
param oidcClientSecret string = ''
@description('Claim with the user\'s email; Entra ID without the email optional claim: preferred_username.')
param oidcEmailClaim string = 'email'
@description('oauth2-proxy cookie secret: exactly 32 random characters (sso mode).')
@secure()
param cookieSecret string = ''

@description('OTLP endpoint for traces (optional), e.g. a collector or Langfuse.')
param otlpEndpoint string = ''

var sso = authMode == 'sso'
var suffix = uniqueString(resourceGroup().id)
var names = {
  logs: '${prefix}-logs-${suffix}'
  environment: '${prefix}-env'
  postgres: '${prefix}-pg-${suffix}'
  redis: '${prefix}-redis-${suffix}'
  api: '${prefix}-api'
  worker: '${prefix}-worker'
  web: '${prefix}-web'
  proxy: '${prefix}-proxy'
  migrate: '${prefix}-migrate'
}

// ---------------------------------------------------------------- monitoring

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: names.logs
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ------------------------------------------------------------------ database

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: names.postgres
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: postgresAdminLogin
    administratorLoginPassword: postgresAdminPassword
    storage: { storageSizeGB: 32, autoGrow: 'Enabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

// pgvector must be allow-listed before migration 0001 can CREATE EXTENSION it.
resource pgExtensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: postgres
  name: 'azure.extensions'
  properties: { value: 'VECTOR', source: 'user-override' }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: 'voc'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
  dependsOn: [pgExtensions]
}

// Connections from Azure services only (0.0.0.0 is Azure's convention for that),
// always over TLS. For a private network instead, see the guide's hardening section.
resource pgFirewall 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

// --------------------------------------------------------------------- queue

resource redis 'Microsoft.Cache/redisEnterprise@2025-04-01' = {
  name: names.redis
  location: location
  sku: { name: 'Balanced_B0' }
  properties: { minimumTlsVersion: '1.2' }
}

resource redisDb 'Microsoft.Cache/redisEnterprise/databases@2025-04-01' = {
  parent: redis
  name: 'default'
  properties: {
    clientProtocol: 'Encrypted'
    port: 10000
    // One endpoint for non-cluster-aware clients (arq / redis-py).
    clusteringPolicy: 'EnterpriseCluster'
    // The queue must not lose jobs to eviction.
    evictionPolicy: 'NoEviction'
    accessKeysAuthentication: 'Enabled'
  }
}

// ------------------------------------------------------------- app settings

var databaseUrl = 'postgresql+asyncpg://${postgresAdminLogin}:${uriComponent(postgresAdminPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/${database.name}?ssl=require'
var redisUrl = 'rediss://:${uriComponent(redisDb.listKeys().primaryKey)}@${redis.properties.hostName}:10000/0'

var registrySecrets = empty(registryUsername) ? [] : [{ name: 'registry-password', value: registryPassword }]
var registries = empty(registryUsername)
  ? []
  : [{ server: registryServer, username: registryUsername, passwordSecretRef: 'registry-password' }]

var appSecrets = concat(
  [
    { name: 'database-url', value: databaseUrl }
    { name: 'redis-url', value: redisUrl }
    { name: 'api-key', value: apiKey }
  ],
  empty(llmApiKey) ? [] : [{ name: 'llm-api-key', value: llmApiKey }],
  registrySecrets
)

var llmKeyEnv = empty(llmApiKey)
  ? []
  : [{ name: llmProvider == 'azure_openai' ? 'AZURE_OPENAI_API_KEY' : 'OPENAI_API_KEY', secretRef: 'llm-api-key' }]
var optionalEnv = filter(
  [
    { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAIEndpoint }
    { name: 'AZURE_OPENAI_API_VERSION', value: azureOpenAIApiVersion }
    { name: 'OTEL_EXPORTER_OTLP_ENDPOINT', value: otlpEndpoint }
  ],
  e => !empty(e.value)
)
var oidcEnv = sso
  ? [
      { name: 'OIDC_ISSUER', value: oidcIssuer }
      { name: 'OIDC_AUDIENCE', value: oidcClientId }
      { name: 'OIDC_EMAIL_CLAIM', value: oidcEmailClaim }
    ]
  : []

// Shared by the API, the worker and the migration job.
var appEnv = concat(
  [
    { name: 'ENVIRONMENT', value: 'production' }
    { name: 'DATABASE_URL', secretRef: 'database-url' }
    { name: 'REDIS_URL', secretRef: 'redis-url' }
    { name: 'API_KEY', secretRef: 'api-key' }
    { name: 'LLM_PROVIDER', value: llmProvider }
    { name: 'LLM_MODEL', value: llmModel }
    { name: 'EMBEDDING_MODEL', value: embeddingModel }
    // Behind the Container Apps ingress (and nginx), the client is the first hop.
    { name: 'RATE_LIMIT_TRUST_FORWARDED_FOR', value: 'true' }
    { name: 'DB_POOL_SIZE', value: '5' }
  ],
  llmKeyEnv,
  optionalEnv,
  oidcEnv
)

// --------------------------------------------------------- container apps

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: names.environment
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

// Apps reach each other at http://<name> inside the environment.
var proxyFqdn = '${names.proxy}.${environment.properties.defaultDomain}'

// Runs `alembic upgrade head`; start it before rolling out a new API image.
resource migrate 'Microsoft.App/jobs@2025-01-01' = {
  name: names.migrate
  location: location
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 1
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      secrets: appSecrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: apiImage
          command: ['alembic', 'upgrade', 'head']
          env: appEnv
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
    }
  }
}

resource api 'Microsoft.App/containerApps@2025-01-01' = {
  name: names.api
  location: location
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: appSecrets
      registries: registries
      ingress: {
        external: exposeApi
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          env: appEnv
          resources: { cpu: json('0.5'), memory: '1Gi' }
          probes: [
            { type: 'Liveness', httpGet: { path: '/health', port: 8000 }, periodSeconds: 10 }
            { type: 'Readiness', httpGet: { path: '/ready', port: 8000 }, periodSeconds: 10 }
          ]
        }
      ]
      // Answers stream over SSE and the agent holds requests for seconds: keep one warm.
      scale: {
        minReplicas: 1
        maxReplicas: 4
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '20' } } }]
      }
    }
  }
  dependsOn: [pgFirewall]
}

resource worker 'Microsoft.App/containerApps@2025-01-01' = {
  name: names.worker
  location: location
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: appSecrets
      registries: registries
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: apiImage
          command: ['arq', 'app.workers.settings.WorkerSettings']
          env: appEnv
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [pgFirewall]
}

resource web 'Microsoft.App/containerApps@2025-01-01' = {
  name: names.web
  location: location
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: concat(sso ? [] : [{ name: 'api-key', value: apiKey }], registrySecrets)
      registries: registries
      // With SSO, only oauth2-proxy can reach the UI.
      ingress: { external: !sso, targetPort: 8080, transport: 'auto', allowInsecure: false }
    }
    template: {
      containers: [
        {
          name: 'web'
          image: webImage
          env: concat(
            [{ name: 'API_UPSTREAM', value: 'http://${api.name}' }],
            // An empty key makes nginx send none; the user's token goes through instead.
            sso ? [{ name: 'API_KEY', value: '' }] : [{ name: 'API_KEY', secretRef: 'api-key' }]
          )
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}

resource proxy 'Microsoft.App/containerApps@2025-01-01' = if (sso) {
  name: names.proxy
  location: location
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: [
        { name: 'client-secret', value: oidcClientSecret }
        { name: 'cookie-secret', value: cookieSecret }
      ]
      ingress: { external: true, targetPort: 4180, transport: 'auto', allowInsecure: false }
    }
    template: {
      containers: [
        {
          name: 'oauth2-proxy'
          image: oauth2ProxyImage
          env: [
            { name: 'OAUTH2_PROXY_HTTP_ADDRESS', value: '0.0.0.0:4180' }
            { name: 'OAUTH2_PROXY_UPSTREAMS', value: 'http://${web.name}' }
            { name: 'OAUTH2_PROXY_PROVIDER', value: 'oidc' }
            { name: 'OAUTH2_PROXY_OIDC_ISSUER_URL', value: oidcIssuer }
            { name: 'OAUTH2_PROXY_CLIENT_ID', value: oidcClientId }
            { name: 'OAUTH2_PROXY_CLIENT_SECRET', secretRef: 'client-secret' }
            { name: 'OAUTH2_PROXY_COOKIE_SECRET', secretRef: 'cookie-secret' }
            { name: 'OAUTH2_PROXY_REDIRECT_URL', value: 'https://${proxyFqdn}/oauth2/callback' }
            { name: 'OAUTH2_PROXY_SCOPE', value: 'openid email profile offline_access' }
            { name: 'OAUTH2_PROXY_OIDC_EMAIL_CLAIM', value: oidcEmailClaim }
            { name: 'OAUTH2_PROXY_CODE_CHALLENGE_METHOD', value: 'S256' }
            // Who may use the app is the API's user list, not the email domain.
            { name: 'OAUTH2_PROXY_EMAIL_DOMAINS', value: '*' }
            { name: 'OAUTH2_PROXY_PASS_AUTHORIZATION_HEADER', value: 'true' }
            { name: 'OAUTH2_PROXY_SKIP_PROVIDER_BUTTON', value: 'true' }
            { name: 'OAUTH2_PROXY_COOKIE_SECURE', value: 'true' }
            { name: 'OAUTH2_PROXY_COOKIE_REFRESH', value: '5m' }
            // The ingress terminates TLS and sets X-Forwarded-*.
            { name: 'OAUTH2_PROXY_REVERSE_PROXY', value: 'true' }
          ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}

// ------------------------------------------------------------------ outputs

@description('Open this in a browser.')
output appUrl string = sso ? 'https://${proxyFqdn}' : 'https://${web.properties.configuration.ingress.fqdn}'

@description('Register this redirect URI with your identity provider (sso mode).')
output redirectUri string = sso ? 'https://${proxyFqdn}/oauth2/callback' : ''

@description('Public API for machine clients (X-API-Key); internal-only when exposeApi is false.')
output apiUrl string = 'https://${api.properties.configuration.ingress.fqdn}'

output migrationJob string = migrate.name
output apiApp string = api.name
output postgresServer string = postgres.name
