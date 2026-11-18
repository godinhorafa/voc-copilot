// Example parameters. Secrets come from environment variables, never this file:
// see docs/deploy/azure-container-apps.md for how to generate them.
using './main.bicep'

param prefix = 'voc'
param apiImage = readEnvironmentVariable('VOC_API_IMAGE', 'ghcr.io/OWNER/voc-copilot-api:latest')
param webImage = readEnvironmentVariable('VOC_WEB_IMAGE', 'ghcr.io/OWNER/voc-copilot-web:latest')

// Private GHCR packages need a token with read:packages; leave empty for public images.
param registryUsername = readEnvironmentVariable('VOC_REGISTRY_USERNAME', '')
param registryPassword = readEnvironmentVariable('VOC_REGISTRY_PASSWORD', '')

param authMode = 'sso'
param exposeApi = true

param apiKey = readEnvironmentVariable('VOC_API_KEY')
param postgresAdminPassword = readEnvironmentVariable('VOC_POSTGRES_PASSWORD')

param llmProvider = 'openai'
param llmModel = 'gpt-4o-mini'
param llmApiKey = readEnvironmentVariable('VOC_LLM_API_KEY', '')

// Microsoft Entra ID app registration (see the guide).
param oidcIssuer = readEnvironmentVariable('VOC_OIDC_ISSUER', '')
param oidcClientId = readEnvironmentVariable('VOC_OIDC_CLIENT_ID', '')
param oidcClientSecret = readEnvironmentVariable('VOC_OIDC_CLIENT_SECRET', '')
param oidcEmailClaim = 'preferred_username'
param cookieSecret = readEnvironmentVariable('VOC_COOKIE_SECRET', '')
