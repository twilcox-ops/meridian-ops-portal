// Meridian Ops Portal -- Azure infrastructure.
//
// Provisions everything the deployed container needs to run as the same
// application code that runs locally (see src/ops_portal/config.py): a
// Container App pulling its image from a Container Registry provisioned
// here, a Postgres Flexible Server for DATABASE_URL, and a Key Vault holding
// the three secret-shaped values the app reads via os.getenv -- resolved at
// the platform level through a user-assigned managed identity, never
// fetched by application code itself. No plaintext secret value is written
// into this file or into main.parameters.json: the three @secure()
// parameters below carry no default and must be supplied at deploy time
// (e.g. `az deployment group create ... --parameters
// postgresAdminPassword=$PG_PASSWORD sessionSecretKey=$SESSION_KEY
// entraClientSecret=$ENTRA_SECRET`), the same way CI would inject them from
// its own secret store.
//
// Verify with `az bicep build --file deploy/main.bicep` -- this template is
// not deployed as part of this change.

targetScope = 'resourceGroup'

// --- parameters ------------------------------------------------------------

@description('Short environment label (e.g. "dev", "staging", "prod"). Used to derive resource names, so keep it lowercase, short, and alphanumeric.')
param environmentName string

@description('Azure region for every resource in this template.')
param location string

@description('Tag of the meridian-ops-portal image (built from deploy/Dockerfile) to deploy. The repository itself lives in the Container Registry this template provisions -- only the tag varies between deploys.')
param containerImageTag string

@description('Administrator username for the Postgres Flexible Server. Never a password: Postgres requires the admin account be provisioned with one, so postgresAdminPassword below carries it instead, as a secure parameter with no default.')
param postgresAdminUsername string

@secure()
@description('Postgres administrator password. No default on purpose -- supply it at deploy time so it never lands in main.parameters.json or in this file.')
param postgresAdminPassword string

@secure()
@description('Value for the SESSION_SECRET_KEY secret (Starlette SessionMiddleware cookie signing -- see .env.example). No default; supply at deploy time.')
param sessionSecretKey string

@secure()
@description('Value for the ENTRA_CLIENT_SECRET secret (the Entra ID app registration\'s confidential-client secret). No default; supply at deploy time.')
param entraClientSecret string

// Not secret-shaped (see config.py's load_config()) -- plain parameters, wired
// into the Container App as regular env values rather than Key Vault
// secretRefs.

@description('Entra ID tenant GUID the app registration lives in (ENTRA_TENANT_ID).')
param entraTenantId string

@description('Entra ID app registration\'s client (application) ID (ENTRA_CLIENT_ID).')
param entraClientId string

@description('OAuth redirect URI Entra ID sends the browser back to after sign-in (ENTRA_REDIRECT_URI). Must exactly match a redirect URI registered on the Entra app registration, and match this Container App\'s own hostname (see the containerAppUrl output) -- so it typically can only be filled in for real after the first deploy produces that hostname.')
param entraRedirectUri string

// .env.example ships these blank with no fallback, and config.py reads them
// with no default either -- there's no sensible fixed value to fall back to,
// so each deployment supplies its own (PROJECT1_INGESTION_SOURCE).
@description('Where project-1\'s ingestion output artifact lives, for the integrations/ adapter to read (PROJECT1_INGESTION_SOURCE). No default in .env.example or config.py, so this deployment must supply one.')
param project1IngestionSource string

@description('Where project-2\'s review-queue output artifact lives (PROJECT2_REVIEW_QUEUE_SOURCE). Same no-default situation as project1IngestionSource above.')
param project2ReviewQueueSource string

@description('Where project-4\'s ticket-triage output artifact lives (PROJECT4_TRIAGE_SOURCE). Same no-default situation as project1IngestionSource above.')
param project4TriageSource string

// --- naming ------------------------------------------------------------
// Key Vault, Container Registry, and Postgres server names must all be
// globally unique across Azure, not just within this resource group.
// uniqueString() derives a deterministic 13-character suffix from the
// resource group and environment name, so re-running this template against
// the same resource group always produces the same names instead of
// colliding with a fresh random one each time.

var resourceToken = uniqueString('${resourceGroup().id}-${environmentName}')
var uamiName = 'id-${environmentName}-ops-portal'
var acrName = take('acr${resourceToken}', 50) // alphanumeric only, no hyphens allowed
var keyVaultName = take('kv-${resourceToken}', 24) // 24-char hard limit
var logAnalyticsName = 'log-${environmentName}-${resourceToken}'
var containerAppEnvName = 'cae-${environmentName}'
var containerAppName = 'ca-${environmentName}-ops-portal'
var postgresServerName = 'psql-${environmentName}-${resourceToken}'
var postgresDatabaseName = 'ops_portal'
var containerImageRepository = 'meridian-ops-portal'

// --- managed identity ------------------------------------------------------
// One identity, assigned to the Container App and granted narrow roles below
// (AcrPull on the registry, Key Vault Secrets User on the vault) -- the
// mechanism that lets the platform resolve Key Vault secretRefs and pull the
// container image without any credential ever being embedded in the app's
// configuration.

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: uamiName
  location: location
}

// --- container registry -----------------------------------------------
// Holds the image built from deploy/Dockerfile. Admin user is disabled --
// the Container App below authenticates to it via the managed identity's
// AcrPull role instead of a shared admin credential.

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource acrPullRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, uami.id, 'AcrPull')
  scope: acr
  properties: {
    // Built-in "AcrPull" role definition ID -- pull-only, no push/manage rights.
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// --- database ------------------------------------------------------------
// Postgres Flexible Server backing DATABASE_URL. Burstable B1ms is the
// smallest general-purpose SKU -- sized for a capstone/demo workload, not
// production traffic. Public network access stays enabled with an
// "allow Azure services" firewall rule rather than VNet integration: the
// Container App environment below has no VNet of its own (its outbound IPs
// are dynamic), so scoping the firewall to specific IPs isn't an option
// without adding VNet integration on both sides -- out of scope here.

resource postgresServer 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: postgresServerName
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '15'
    administratorLogin: postgresAdminUsername
    administratorLoginPassword: postgresAdminPassword
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
  }
}

// database.py / db/base.py connect to a single named database, not the
// server's default "postgres" database.
resource postgresDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview' = {
  parent: postgresServer
  name: postgresDatabaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Special "0.0.0.0-0.0.0.0" range is Azure's documented shorthand for
// "allow connections from any Azure-hosted resource, in any subscription" --
// the trade-off called out above. Concretely: this opens the server's
// network-level door to any Azure-hosted resource anywhere, in any Azure
// subscription belonging to anyone, not just this deployment's own
// Container App -- a valid administratorLogin/administratorLoginPassword (or
// AAD credential) is still required for a connection attempt to actually
// succeed, so this rule alone grants no access to data, but it is
// meaningfully broader than scoping to this deployment's own traffic.
// Tightening this to VNet integration (a VNet-integrated Container Apps
// environment plus a matching delegated subnet for the Postgres server,
// with the firewall rule above replaced by that private link) is a known,
// deliberate deferral for this template -- not an oversight -- kept out for
// the same reason called out above: it doubles the networking surface of
// what is otherwise a capstone/demo-sized deployment.
resource postgresFirewallAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  parent: postgresServer
  name: 'AllowAllAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// --- key vault -----------------------------------------------------------
// Holds the three secret-shaped values ops_portal/config.py reads via
// os.getenv: DATABASE_URL, SESSION_SECRET_KEY, ENTRA_CLIENT_SECRET. RBAC
// authorization (not the legacy access-policy model) so access is granted
// with the same Microsoft.Authorization/roleAssignments mechanism used for
// the registry above.

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
  }
}

resource keyVaultSecretsUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, uami.id, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    // Built-in "Key Vault Secrets User" role definition ID -- get/list on
    // secrets only, no write access; the Container App only ever reads.
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault secret names allow only alphanumerics and hyphens, hence the
// hyphenated names here -- the Container App's secret block below maps each
// back to the underscored env var name the app actually reads.

resource databaseUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'database-url'
  properties: {
    // Same postgresql+psycopg2:// scheme as .env.example and
    // docker-compose.local.yml -- psycopg2-binary is what deploy/Dockerfile
    // installs via the [postgres] extra.
    value: 'postgresql+psycopg2://${postgresAdminUsername}:${postgresAdminPassword}@${postgresServer.properties.fullyQualifiedDomainName}:5432/${postgresDatabaseName}?sslmode=require'
  }
}

resource sessionSecretKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'session-secret-key'
  properties: {
    value: sessionSecretKey
  }
}

resource entraClientSecretSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'entra-client-secret'
  properties: {
    value: entraClientSecret
  }
}

// --- container app environment --------------------------------------------
// Log Analytics workspace + Container Apps managed environment: the shared
// hosting boundary a Container App revision runs inside. No VNet integration
// (see the Postgres firewall comment above) -- this is the default
// platform-managed networking, which is what makes the *.azurecontainerapps.io
// managed certificate below work with no extra configuration.

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: containerAppEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// --- container app ---------------------------------------------------------
// Runs the image built from deploy/Dockerfile (CMD there listens on 8000,
// hence targetPort below). ingress.external + allowInsecure: false is what
// gives the app HTTPS on its default *.azurecontainerapps.io hostname via
// the platform's managed certificate -- no customDomains block, no
// certificate resource: that's the "don't attempt custom domain/cert
// provisioning" default, and it's automatic, not something this template
// has to request.

resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        allowInsecure: false
        transport: 'auto'
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: uami.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: databaseUrlSecret.properties.secretUri
          identity: uami.id
        }
        {
          name: 'session-secret-key'
          keyVaultUrl: sessionSecretKeySecret.properties.secretUri
          identity: uami.id
        }
        {
          name: 'entra-client-secret'
          keyVaultUrl: entraClientSecretSecret.properties.secretUri
          identity: uami.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'ops-portal'
          image: '${acr.properties.loginServer}/${containerImageRepository}:${containerImageTag}'
          resources: {
            // Smallest allowed Consumption-plan allocation -- a capstone/demo
            // workload, not a sized-for-production estimate.
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'SESSION_SECRET_KEY'
              secretRef: 'session-secret-key'
            }
            {
              name: 'ENTRA_CLIENT_SECRET'
              secretRef: 'entra-client-secret'
            }
            {
              name: 'ENTRA_TENANT_ID'
              value: entraTenantId
            }
            {
              name: 'ENTRA_CLIENT_ID'
              value: entraClientId
            }
            {
              name: 'ENTRA_REDIRECT_URI'
              value: entraRedirectUri
            }
            {
              name: 'PROJECT1_INGESTION_SOURCE'
              value: project1IngestionSource
            }
            {
              name: 'PROJECT2_REVIEW_QUEUE_SOURCE'
              value: project2ReviewQueueSource
            }
            {
              name: 'PROJECT4_TRIAGE_SOURCE'
              value: project4TriageSource
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
  // Ingress-assigned identity needs the AcrPull / Key Vault Secrets User
  // roles in place before the platform tries to use them to pull the image
  // or resolve secretRefs on first revision creation.
  dependsOn: [
    acrPullRoleAssignment
    keyVaultSecretsUserRoleAssignment
  ]
}

// --- outputs ---------------------------------------------------------------

@description('Public HTTPS URL of the deployed Container App (its default *.azurecontainerapps.io hostname).')
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'

@description('Login server of the provisioned Container Registry -- where CI pushes the meridian-ops-portal image before a deploy.')
output containerRegistryLoginServer string = acr.properties.loginServer
