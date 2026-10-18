# Production deployment and cost guide

This guide describes the current Docker Compose production stack and practical
ways to host it. It is an operator guide, not a security certification or a
provider quotation. Re-check provider prices, regional availability, tax, and
healthcare/privacy obligations before committing to a service.

## Cost target and recommended starting point

The application can serve multiple hospital tenants from one deployment: the
control plane and API are shared, while each registered tenant has a separate
PostgreSQL database. Ten tenants do not inherently require ten virtual
machines. CPU, memory, storage, database growth, backup retention, traffic, and
availability requirements drive the bill.

| Annual budget | Practical interpretation |
| --- | --- |
| **₹0 cash spend** | Possible only for an internal demo on hardware, power, network, domain, and operations you already pay for. It has no meaningful uptime/recovery guarantee. Use synthetic data only; do not put live clinical data on a free tier or an unmanaged home/office host. |
| **₹1,000/year** | Not a realistic budget for a dependable public production deployment. It is roughly ₹83/month, before a domain, storage, backups, tax, or egress. Free tiers and credits can expire, sleep, change limits, or delete data; do not use them for live clinical data. |
| **Up to ₹1,00,000/year** | A lean, single-VM Docker deployment may fit if the selected region/instance, storage, backup storage, and bandwidth fit the monthly cap. This is a **single point of failure**, not high availability. Set a hard monthly budget alert and check actual cloud calculator estimates before provisioning. |
| **Higher availability / managed services** | Separate managed PostgreSQL, redundant app instances, multi-zone networking, WAF, monitoring, and retained off-site backups can exceed ₹1,00,000/year. Price these separately; managed databases and high availability frequently dominate the bill. |

### Suggested low-cost phases for ten hospitals

1. **Demo / design-partner validation, no real patient data:** use an existing
   workstation or the cheapest short-lived VM, Docker Compose, and synthetic
   records. Keep it explicitly non-production. This is the only credible
   near-zero-cash option; it is not a live hospital service.
2. **First live hospital:** one maintained Linux VM near the hospital, with
   separate PostgreSQL databases per tenant, encrypted storage, TLS, tightly
   limited operator access, off-host encrypted backups, monitoring, and a
   successful restore drill. Pay for these minimum safeguards instead of
   calling a free tier production.
3. **Up to ten hospitals:** keep one shared app/control-plane deployment and
   one PostgreSQL server with per-tenant databases while representative load
   tests and production telemetry show the host has headroom. Avoid one VM per
   hospital. Review capacity, support coverage, recovery objectives, privacy
   obligations, and the monthly bill at every onboarding; do not promise
   availability beyond the single-host design.
4. **Scale / availability upgrade:** move the database and backup storage to
   managed or independently hosted services, then add redundant app instances,
   shared cache, point-in-time recovery, and tested failover. Re-price and
   test the target topology before the move.

The cloud bill cannot honestly be called zero once live patient data, public
availability, off-host recovery, and operational support are required. To keep
cost low, postpone Kubernetes, per-tenant infrastructure, an ALB, and managed
database only while a documented risk review accepts the single-host failure
mode. A second copy of backups on the same VM is not disaster recovery.

### Production go-live and scale gates

Before real data: use a reviewed immutable release; deploy to an isolated
staging copy; run the backend and UI tests; verify tenant-isolation and access
controls; test provisioning and billing integrations that are enabled; confirm
the production secret file is protected; restore an off-host backup into an
isolated database; and document incident contacts, recovery objectives, and
the applicable healthcare/privacy review. Do not treat a green container
health check as approval to go live.

For each tenant schema change: back up and verify the target, apply the reviewed
control-plane revision in its planned window, migrate one named canary tenant,
verify its workflows and logs, then obtain explicit fleet approval before
running the fleet command. Production `db-init` does not migrate existing
tenants at every restart; it only migrates the control plane and initializes a
new default tenant. It verifies existing active tenant revisions and schema
parity against the release and blocks startup if any tenant is behind. Routine
restarts therefore cannot accidentally bypass the canary gate.

Review scale using measured CPU and memory headroom, database/storage growth,
backup duration, API latency/error rates, and restore time—not hospital count
alone. Upgrade when load tests or service objectives show a bottleneck, when
the single failure domain is no longer acceptable, or when a customer or
regulatory obligation requires stronger isolation/availability. Rehearse the
upgrade and rollback before changing production.

Avoid Kubernetes, one managed database per hospital, and a separate cache or
load balancer per tenant for the initial ten-tenant target unless a measured
requirement justifies the cost. Do not mix tenants into one database schema as
a cost shortcut: this application provisions separate tenant databases.

### Provider choices

The same production Compose stack can run on a Linux virtual machine from AWS,
Azure, GCP, or another VM provider. Choose a region near users, compare
equivalent CPU/RAM/disk/network offerings, and calculate the full monthly
bill. For the stated cap, start by comparing **one VM plus attached disk**:

| Provider | Lean deployment path | Higher-cost managed alternative |
| --- | --- | --- |
| AWS | EC2 VM + attached block storage + Docker Compose/Traefik. Use a security group and budget alarms. | ECS/Fargate or EKS, RDS/Aurora, load balancer, NAT, and CloudWatch. |
| Azure | Linux Virtual Machine + managed disk + Docker Compose/Traefik. Use NSG rules and Cost Management alerts. | Container Apps/AKS, Azure Database for PostgreSQL, Application Gateway, and Monitor. |
| GCP | Compute Engine VM + persistent disk + Docker Compose/Traefik. Use firewall rules and budget alerts. | Cloud Run/GKE, Cloud SQL for PostgreSQL, load balancer, and Cloud Monitoring. |
| Other VM / Docker host | A supported Linux VM with a static public IP and Docker Engine/Compose. Verify provider backups, security updates, and support. | Provider-specific managed database and container offerings. |

Managed container products do not necessarily run this Compose file unchanged.
The included production file assumes Docker labels and Traefik's Docker
provider, so for managed containers replace Traefik with that provider's
ingress/load balancer, provide equivalent host routing and TLS, and preserve
separate tenant database routing. Budget alerts are alerts, not hard spending
caps.

## Production prerequisites

- A Linux VM with Docker Engine and the Docker Compose plugin. Size it only
  after testing representative API concurrency, provisioning, database growth,
  and backups; 2 vCPU / 8 GB RAM is a **starting test profile**, not a capacity
  guarantee. Increase it if load tests or memory usage require it.
- A static public IP, a domain, and DNS. Point the base domain and wildcard
  tenant domain (`*.<BASE_DOMAIN>`) to the VM. Point `onboard.<BASE_DOMAIN>` to
  the same VM. The production Traefik configuration uses Cloudflare DNS
  challenge; provide a Cloudflare DNS API token with only the required zone
  DNS permissions. If using a different DNS provider, replace the Traefik DNS
  challenge provider configuration.
- Inbound firewall rules for TCP 80 and 443. Restrict SSH to operator IPs.
  PostgreSQL and Redis must not be publicly reachable.
- An operational `.env.production` created on the host from a secret manager
  or secure local process. Never copy the development `.env` into production,
  put secrets in Git, or paste secret values in tickets/logs.
- Before starting Compose, restrict the file to the deployment account and
  run `chmod 600 .env.production` followed by
  `python -m backend.scripts.validate_production_env --env-file .env.production`.
  The validator reports variable names and errors, never secret values. On
  Windows, use filesystem ACLs to restrict access; the Unix mode check does
  not apply there.
- Production uses `BACKUP_HOST_PATH` for the host cron workflow and
  `ATTACHMENTS_HOST_PATH` for the API bind mount. Use separate directories on
  one attached data disk for combined storage, or mount different disks at
  those paths for separate storage. `ATTACHMENTS_STORAGE_DIR` must match the
  container mount (`/app/attachments` in the supplied Compose file).
  Procurement invoice files are now retained there under tenant-specific
  directories; include them in encrypted host backups and verify restoration.
  Uploads are limited to PDF/PNG/JPEG and 20 MiB by default, but signature
  checks do not replace malware scanning. The app still lacks general clinical
  attachment support, durable download auditing, and retention/deletion
  controls.
- Configure the disk mount persistently (for example by UUID in `/etc/fstab`)
  and verify it with `findmnt` before starting Compose or cron. The backup job
  checks `BACKUP_STORAGE_MOUNTPOINT` and refuses to write a backup under the
  root filesystem when the attached backup disk is absent.
- Off-host encrypted backups with retention and a restore test. The included
  `deployments/backup-postgres.sh` script encrypts PostgreSQL dumps with GPG
  and stores them under `BACKUP_HOST_PATH`; optional `BACKUP_REMOTE_DEST`
  copies encrypted snapshots over rsync/SSH to a separate host or NAS. A
  second disk attached to the same VM is not off-host disaster recovery. The
  remote target, SSH key, GPG passphrase, retention, and restore drill must be
  managed by the operator.
- A change/rollback plan for Alembic migrations. Production startup applies
  control-plane migrations and initializes a new default tenant; existing
  tenant migrations require the documented canary and explicit rollout.

## AWS deployment: EC2 with or without an Application Load Balancer

For a cost-conscious first production deployment, use one EC2 Linux instance
running the current Compose stack. This keeps the API, onboarding portal,
Traefik, PostgreSQL, and Redis on one host; PostgreSQL and Redis remain on the
Compose internal network and are not exposed to the internet. One EC2 instance
is a single point of failure even if an ALB is placed in front of it.

### Create the AWS foundation

1. Select a region close to the hospitals and create a VPC with at least two
   public subnets in different Availability Zones if you may add an ALB. For a
   single EC2 without an ALB, one public subnet is technically sufficient, but
   a second AZ makes later expansion easier. Keep databases private; do not
   create a public RDS or expose PostgreSQL/Redis ports.
2. Create an EC2 security group. For direct-to-EC2 routing, allow inbound TCP
   80 and 443 from the internet and SSH (22) only from a fixed operator IP or
   use AWS Systems Manager Session Manager and omit inbound SSH. For ALB
   routing, allow 80 from the ALB security group only; allow 443 from the ALB
   security group only if you intentionally use HTTPS between ALB and Traefik.
   Never allow inbound 5432 or 6379.
3. Launch a supported Ubuntu LTS or Amazon Linux EC2 instance with an encrypted
   EBS volume, a static Elastic IP when there is no ALB, and an EC2 instance
   profile. Attach the least-privilege instance role described under Secrets
   below, enable IMDSv2, and require IMDSv2 tokens. Install Docker Engine and the Docker Compose plugin using the
   distribution's official installation instructions.
4. Check out a reviewed private-repository release, install the production
   Compose file, and configure `.env.production` as described in this guide.
   Put PostgreSQL data and local backup volumes on encrypted EBS. Do not treat
   EBS snapshots or the local backup volume as an independent off-site backup.
5. In Route 53, create records for the base domain, `*.BASE_DOMAIN`, and
   `onboard.BASE_DOMAIN`. Point all three at the same entry point: an Elastic
   IP for direct EC2 routing or an ALB alias for load-balanced routing. A
   wildcard record is required because every tenant is addressed by its slug.
   The DNS zone may be hosted in Route 53 even if the registrar is elsewhere;
   update the domain's authoritative name servers at the registrar.
6. Confirm public DNS resolution, inbound firewall rules, TLS issuance, health
   checks, tenant provisioning, tenant login, and database isolation before
   loading real patient data.

### Option A: Route 53 directly to EC2 (lowest AWS network cost)

```text
Browser ── HTTPS :443 ──> Route 53 A/AAAA ──> Elastic IP / EC2
                                              └─ Traefik TLS + host routing
                                                  ├─ API (base + tenant hosts)
                                                  └─ onboarding portal
```

Use an Elastic IP so an instance replacement does not silently change DNS.
The production Compose configuration is designed for this topology: Traefik
listens on host ports 80/443, obtains certificates through the configured
DNS-01 provider, and routes tenant hostnames to the API and the onboarding
hostname to the control plane. If using Route 53 DNS-01 instead of Cloudflare,
change the Traefik DNS challenge provider and provide a narrowly scoped Route
53 policy for the required hosted zone; the current production Compose file
expects Cloudflare DNS credentials and must be changed before that alternative
will work.

Keep the EC2 security group limited to 80/443 and trusted operator access.
There is no separate load-balancer health check or second app instance in this
option. Configure EC2 recovery/monitoring, EBS snapshots, off-site backup
copying, and restore drills; a host outage takes down all tenants.

### Option B: public ALB in front of EC2

```text
Browser ── HTTPS :443 ──> Route 53 alias ──> internet-facing ALB
                                              ├─ onboard.BASE_DOMAIN
                                              ├─ BASE_DOMAIN
                                              └─ *.BASE_DOMAIN
                                                   │ HTTP in private subnet
                                                   v
                                              EC2 / Traefik :80
                                              ├─ API
                                              └─ onboarding portal
```

1. Request or import an ACM certificate in the **same region as the ALB** for
   `BASE_DOMAIN`, `*.BASE_DOMAIN`, and `onboard.BASE_DOMAIN` (the wildcard
   does not cover the base domain). Complete DNS validation in Route 53.
2. Create an internet-facing ALB in public subnets in at least two AZs. Add an
   HTTPS 443 listener using the ACM certificate and a redirect from HTTP 80 to
   HTTPS 443. Add a target group for the EC2 host's Traefik port 80 and an
   HTTP health check. Add a Traefik health route matching `/health` so the
   target health check can reach the API health endpoint without depending on
   an application hostname. For example, configure a Traefik router on its
   `web` entry point with rule `Path(`/health`)` and service `hms-api`; the API
   service must still target the configured API container port.
3. Add ALB host-header listener rules for `onboard.BASE_DOMAIN`, then
   `BASE_DOMAIN` and `*.BASE_DOMAIN`; forward them to the same target group.
   Traefik continues routing the hostnames to the API or onboarding portal.
4. Change the EC2 security group so port 80 accepts traffic only from the ALB
   security group. Do not leave a public EC2 80/443 rule as a bypass. Route 53
   A/AAAA alias records for all three names should target the ALB, not EC2.
5. Configure Traefik for **HTTP behind the ALB**: remove its public port 443
   exposure and ACME/TLS termination, use its HTTP entry point for the
   routers, and do not keep an HTTP-to-HTTPS redirect on the EC2 hop (that
   would loop). The ALB is the public TLS boundary. Forward the original host
   and `X-Forwarded-Proto` headers and configure trusted proxy handling rather
   than trusting arbitrary forwarded headers from the public internet.
6. Keep target-group health status, ALB access logs, and application health
   monitoring enabled. The ALB provides TLS termination and a place to add
   targets, but **one EC2 target is still one failure domain**. Real
   multi-instance availability requires at least two app instances across AZs
   and shared/external PostgreSQL, Redis, backup storage, and secrets. Do not
   put stateful Compose databases on independent EC2 hosts and expect them to
   stay synchronized.

An ALB adds recurring cost and does not fit every low-budget deployment. Use
direct EC2 routing for a controlled pilot when its single-host availability
trade-off is acceptable; choose an ALB for managed TLS/host routing or before
adding redundant application targets. Compare current AWS prices for the ALB,
LCU usage, public IPv4 addresses, NAT gateways, data transfer, EBS, Route 53,
and logging. Avoid a NAT gateway for the initial single-public-subnet host
without checking the fixed monthly charge; tightly scope outbound routes and
host firewall instead.

### AWS secrets and instance permissions

Use AWS Secrets Manager (or Systems Manager Parameter Store `SecureString`)
for production secret storage. Store each environment variable as a key in a
single JSON secret or in separate secrets. Attach an EC2 instance profile with
only `secretsmanager:GetSecretValue` for that secret ARN and `kms:Decrypt` only
if a customer-managed KMS key is used. Use a separate, least-privilege
instance profile policy for off-site backup uploads; do not put static AWS
access keys in `.env.production`.

The current Compose file consumes a root-readable `.env.production` file; it
does **not** fetch Secrets Manager values by itself. Before starting Compose,
use the instance role and a protected deployment/bootstrap step to materialize
the secret on the encrypted host filesystem with owner root and mode `0600`.
Do not print secret values, pass them as command-line arguments, commit the
file, bake it into an image, or expose it through user-data. Restrict operator
access with IAM/SSM, audit secret reads with CloudTrail, and rotate credentials
through a planned restart. Keep the Master Setup Key, control-plane secret,
database password, JWT/envelope keys, Redis password, SMTP/payment credentials,
and DNS API token distinct.

For example, use SSM Session Manager or an audited deployment job to:

```text
1. Assume the EC2 instance role; retrieve the named secret (never echo it).
2. Validate required environment-variable names and write .env.production
   under /opt/avinya-hms with owner root and mode 0600.
3. Run Compose config validation without printing resolved config.
4. Start/update Compose and verify health checks.
5. Remove temporary secret material and retain only the protected runtime
   environment file required by the current Compose env_file configuration.
```

If `SecretString` contains valid dotenv lines, the deployment step can write
them without displaying the value:

```sh
sudo bash -c 'set -euo pipefail; umask 077; aws secretsmanager get-secret-value \
  --secret-id avinya/production/compose-env --query SecretString --output text \
  > /opt/avinya-hms/.env.production; chown root:root /opt/avinya-hms/.env.production; chmod 600 /opt/avinya-hms/.env.production'
cd /opt/avinya-hms
sudo docker compose -f docker-compose.prod.yml --env-file .env.production config --quiet
```

Use an EC2 role that can read only this secret. This command assumes the secret
contains valid Compose dotenv content; if using a JSON key/value secret, use a
reviewed conversion step that correctly quotes values instead of printing or
manually copying them. Run Docker Compose through a restricted deployment
account that can read the protected env file, or through a root-owned service.

Do not use EC2 user-data to hold secret values: user-data is retrievable from
the instance metadata service and is commonly retained in deployment records.
Enable IMDSv2, restrict metadata access, and use an instance profile rather
than access keys.

### AWS backup storage

The included host cron workflow writes encrypted PostgreSQL 17 snapshots to
`BACKUP_HOST_PATH`, an encrypted attached disk for local recovery. To protect
against VM loss, configure a separately administered Linux host/NAS and
`BACKUP_REMOTE_DEST` for rsync/SSH replication. This is the selected low-cost
path; no S3 integration is implemented. Keep remote credentials and the GPG
passphrase outside the repository, enforce remote retention, monitor backup
freshness, and test restoring from the remote copy.

### Production environment variables

Copy `.env.production.example` to a host-local `.env.production` and replace
all placeholders. Populate all settings required by `backend/core/config.py`.
The list below intentionally contains no production secret values.
`docker-compose.prod.yml` also requires `CF_DNS_API_TOKEN` and reads
`.env.production` both for Compose substitutions and inside the relevant
containers.

| Purpose | Variables |
| --- | --- |
| URLs and identity | `ENVIRONMENT=production`, `DEVELOPMENT_AUTO_CREATE_SCHEMA=false`, `PLATFORM_NAME`, `BASE_DOMAIN`, `DEFAULT_TENANT_SLUG`, `DEFAULT_HOSPITAL_NAME`, `DEFAULT_BRANCH_CODE`, `VERSION`, `API_PORT`, `API_SERVICE_HOST`, `CONTROL_PLANE_PORT`, optional `TENANT_URL_TEMPLATE` |
| Runtime and tenant DB naming | `GUNICORN_WORKERS`, `TENANT_DATABASE_PREFIX`, `APP_TIMEZONE`, `MAX_PAGE_SIZE`, `MIGRATION_PREVIEW_ROWS`, `MIGRATION_PREVIEW_ERROR_LIMIT`, `MIGRATION_MAX_UPLOAD_BYTES`, `MIGRATION_MAX_EXPRESSION_DEPTH`, `MIGRATION_MAX_INPUTS` |
| Backup/cache tuning | `BACKUP_INTERVAL_SECONDS`, `BACKUP_OUTPUT_DIR`, `VAULT_POLL_INTERVAL_SECONDS`, `REDIS_MAX_MEMORY` |
| PostgreSQL | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST=db`, `POSTGRES_PORT=5432`, `CONTROL_PLANE_DB_NAME` |
| Redis | `REDIS_HOST=redis`, `REDIS_PORT=6379`, `REDIS_DB`, `REDIS_PASSWORD` |
| Security | `SECRET_KEY` (at least 32 characters), `MASTER_ONBOARDING_KEY` (at least 16 characters), `CONTROL_PLANE_MASTER_KEY` (recommended), `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `ENVELOPE_ENCRYPTION_SALT`, `BACKUP_ENCRYPTION_SALT` |
| Initial account and CORS | `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD`, `CORS_ORIGINS` (comma-separated exact trusted origins, including `https://onboard.<BASE_DOMAIN>`) |
| Mail and integrations | `MAIL_PROVIDER` (`smtp`, `aws_ses`, or `azure_communication_services`), `SMTP_FROM`; SMTP additionally uses `SMTP_HOST`, `SMTP_PORT`, optional `SMTP_USER`/`SMTP_PASSWORD`; SES uses `AWS_SES_REGION` and the AWS SDK credential chain/role; Azure uses `AZURE_COMMUNICATION_CONNECTION_STRING` and optional `AZURE_COMMUNICATION_SENDER`; optional `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` enable Stripe webhook handling |
| Proxy/certificate | `CF_DNS_API_TOKEN`, `ACME_EMAIL`, `ACME_DNS_PROVIDER`, `ACME_DNS_RESOLVERS`, `TRAEFIK_HTTP_PORT`, `TRAEFIK_HTTPS_PORT`, `PUBLIC_HTTP_PORT`, `PUBLIC_HTTPS_PORT` |
| Local Compose-only port bindings | `POSTGRES_PUBLISHED_PORT`, `REDIS_PUBLISHED_PORT`, `API_PUBLISHED_PORT`, `CONTROL_PLANE_PUBLISHED_PORT` |

Use unique, randomly generated production values for all secrets. Keep the
control-plane key separate from the Master Setup Key. Enter them only in the
trusted onboarding browser; the current UI stores entered keys in browser
local storage. Rotate any credential that has been shared in source control,
chat, an IDE screenshot, or an untrusted browser. Configure billing and cloud
integration credentials only if those integrations are enabled.

The current application does not consume `KMS_INTERNAL_ROOT_KEY` or
`KMS_INTERNAL_AUTH_TOKEN`; entries with those names in older local environment
files do not provide active KMS protection. Do not rely on them as a security
control.

Outbound onboarding email must not be used to distribute passwords, signing
keys, federation credentials, or backup recovery keys. Provisioning sends only
non-secret setup details; retrieve one-time credentials from the authorized
vendor console. A failed welcome email does not undo tenant provisioning and
is reported in the provisioning result and control-plane audit ledger.

### Invoices and insurer document exports

Tenant invoice HTML templates can be customized using the report-template
endpoints and selected with `template_code`. Download PDFs with
`GET /v1/api/billing/invoices/{invoice_id}/pdf`; the corresponding `/print`
endpoint continues to return HTML for browser printing. The A4 and 80mm
templates use the same invoice data.

Set tenant-specific invoice identity and display details through
`PUT /v1/api/admin/policies`, using the `invoice_document_profile` object:
`legal_entity_name`, `address_lines`, `currency_code`, `currency_locale`,
`minor_unit_digits`, optional `currency_symbol`, `tax_registrations`,
`tax_note`, and `payment_terms`. Amounts are rendered from the integer minor
units already stored by billing; configure service and payer tariff prices in
the same minor-unit scale. Tax registration fields and notes are document
metadata only: the current billing ledger does **not** calculate jurisdiction
taxes, compound taxes, exemptions, or tax-on-discount rules. Confirm those
rules and the final invoice with local tax/accounting counsel before using the
output as a statutory tax invoice.

Insurance providers can store their payer-specific required-document
checklist in `document_requirements` when created or through
`PATCH /v1/api/insurance/providers/{provider_id}`. Export supporting summaries
as PDFs from
`GET /v1/api/insurance/preauths/{preauth_id}/documents.pdf` and
`GET /v1/api/insurance/claims/{claim_id}/documents.pdf`; use `template_code`
to select a customized report template. These are adaptable supporting
summaries and checklists, not insurer-issued forms or guarantees of acceptance.
Verify the provider's current forms, signatures, and submission rules before
filing. Generated PDFs do not fetch external template resources.

`MAIL_PROVIDER=aws_ses` and `MAIL_PROVIDER=azure_communication_services` use
the optional SDKs in `requirements-integrations.txt`. Verify sender identity,
regional delivery limits, bounce handling, and service credentials with the
provider. Do not store cloud credentials in source control; prefer an
instance/workload role for SES and a secret manager for the Azure connection
string.

## Deploy with Docker Compose on a Linux VM

The following commands are run on the VM. Replace the repository URL and
directory for your deployment; pin a reviewed release/tag or commit rather
than deploying an arbitrary branch.

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
# Install Docker Engine and the Compose plugin using the official instructions
# for your Linux distribution, then log out/in or use sudo for Docker.

sudo install -d -m 0750 /opt/avinya-hms
sudo chown "$USER":"$USER" /opt/avinya-hms
git clone <YOUR-PRIVATE-REPOSITORY-URL> /opt/avinya-hms
cd /opt/avinya-hms
git checkout <REVIEWED-RELEASE-OR-COMMIT>
umask 077
cp .env.production.example .env.production
${EDITOR:-vi} .env.production
chmod 600 .env.production
```

Replace every placeholder before deployment; the example file is not a
production secret source.

Before starting, check the DNS A/AAAA records and confirm the host firewall
does not expose ports 5432 or 6379. Validate and deploy:

```sh
docker compose -f docker-compose.prod.yml --env-file .env.production config --quiet
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
docker compose -f docker-compose.prod.yml --env-file .env.production ps
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail=150 db-init api onboarding-portal
curl --fail --show-error https://<BASE_DOMAIN>/health
curl --fail --show-error https://onboard.<BASE_DOMAIN>/docs
```

Open `https://onboard.<BASE_DOMAIN>` for the onboarding console. A newly
provisioned tenant URL is `https://<slug>.<BASE_DOMAIN>`; `/docs` is the API
Swagger page, not a hospital user interface. Use a real browser check to
confirm wildcard DNS, TLS, API health, onboarding authentication, tenant
provisioning, tenant login, and database separation.

### Updates and rollback

1. Take and verify off-host backups before a release.
2. Review tenant and control-plane Alembic revisions; test upgrades against a
   recent database copy and keep the previous image/release available.
3. Deploy a pinned release:

   ```sh
   git fetch --tags
   git checkout <NEXT-REVIEWED-RELEASE>
   docker compose -f docker-compose.prod.yml --env-file .env.production config --quiet
   docker compose -f docker-compose.prod.yml --env-file .env.production build
   docker compose -f docker-compose.prod.yml --env-file .env.production run --rm --no-deps api python backend/scripts/migrate.py --default-only
   # Verify the default/canary tenant and obtain explicit approval before:
   docker compose -f docker-compose.prod.yml --env-file .env.production run --rm --no-deps api python backend/scripts/migrate.py --fleet
   docker compose -f docker-compose.prod.yml --env-file .env.production up -d
   docker compose -f docker-compose.prod.yml --env-file .env.production ps
   docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail=200 db-init api
   ```
   Skip the one-off tenant migration commands when the release has no tenant
   schema changes. After `up`, `db-init` checks revisions and schema parity
   across active tenants and prevents the API from starting if rollout was
   incomplete.
4. If the migration failed, stop and investigate the migration logs. Do not
   repeatedly recreate containers or manually stamp Alembic. Application
   rollback alone may not reverse a database migration; restore or apply a
   separately reviewed forward fix when required.

### Backups and recovery

Run `deployments/backup-postgres.sh` from the Linux deployment host on a
scheduled cron job. It exports the control-plane DB and tenant DBs matching
`TENANT_DATABASE_PREFIX`, encrypts each dump with GPG, checks archive lists and
checksums, keeps 14 days locally by default, and optionally copies encrypted
snapshot folders to a remote host using rsync/SSH. See
`docs/OPERATIONS_RUNBOOK.md` for setup, cron, restore commands, and attached
volume layouts. An attached disk remains in the VM failure domain; off-host
protection requires the remote copy.

Inspect service state and recent logs:

```sh
docker compose -f docker-compose.prod.yml --env-file .env.production ps
tail -n 200 /var/log/avinya-backup.log
```

Restore to a `restore_*` database using `deployments/restore-postgres.sh`, then
verify Alembic revision, schema parity, representative data, tenant isolation,
and application workflows. Never overwrite a live DB in an initial restore
test. Record actual recovery time and latest-restorable timestamp to establish
measured RTO and RPO.

## Copy a clean project tree (Windows PowerShell)

`robocopy` can copy the working tree while excluding Git metadata,
virtual environments, caches, node packages, build output, local databases,
backups, and secret env files. Set a new destination that is not inside the
source repository:

```powershell
$source = (Resolve-Path 'V:\avinya_hms').Path
$destination = 'D:\deploy\avinya_hms'

robocopy $source $destination /E /COPY:DAT /DCOPY:DAT /R:2 /W:2 `
  /XD .git .venv venv env __pycache__ .pytest_cache .mypy_cache .ruff_cache `
      node_modules dist build coverage htmlcov backups `
  /XF .env .env.* *.pyc *.pyo *.log *.sqlite *.sqlite3 *.db *.dump *.dump.enc

if ($LASTEXITCODE -ge 8) { throw "Robocopy failed with exit code $LASTEXITCODE" }
```

This preserves source files, migrations, tests, docs, and configuration. Keep
`__init__.py`: it is application source that makes Python packages importable;
the generated directory `__pycache__` and bytecode files (`*.pyc`) are what
should be excluded. This command copies files present in the working tree,
including untracked source files; review the destination and never copy local
secrets there.

On Linux/macOS, an equivalent `rsync` command is:

```sh
rsync -a --exclude='.git/' --exclude='.venv/' --exclude='venv/' \
  --exclude='__pycache__/' --exclude='*.py[cod]' --exclude='.pytest_cache/' \
  --exclude='node_modules/' --exclude='dist/' --exclude='build/' \
  --exclude='backups/' --exclude='.env' --exclude='.env.*' \
  --exclude='*.sqlite*' --exclude='*.db' --exclude='*.dump*' \
  ./ <DESTINATION>/
```

## Operational limits and security

- One Compose VM is a cost-saving starting point only. VM/disk loss can take
  down all ten hospitals; off-site backups reduce data-loss risk, not downtime.
- Apply OS and Docker security updates, restrict operator access, review
  firewall/TLS settings, alert on disk/memory/backup/migration failures, and
  rehearse recovery. Protect the Docker socket and secrets.
- Before storing real patient data, obtain a security and privacy review and
  determine applicable Indian and other data-protection, medical-record,
  residency, retention, breach-reporting, and contractual requirements. This
  guide does not establish compliance.
- Run representative load tests with the expected concurrent users, data
  retention, reports, scheduled jobs, and backup sizes. Ten tenant records
  alone do not predict resource requirements.

## No-additional-service improvements for the local Docker setup

These improvements use the existing application, PostgreSQL, Redis, and
Compose services; they do not require a paid cloud queue, managed database,
load balancer, or object-storage account:

- Use the Migration Studio's CSV preview, safe expression blocks, single-field
  duplicate policies, row counts, and audit-ledger outcome records. Follow
  `docs/MIGRATION_STUDIO.md`; keep imports small until migrations run as durable
  jobs.
- Keep pagination and import limits in `backend/core/config.py` and override
  them through environment variables instead of scattering deployment limits
  through UI/API code. The stable setting names/defaults are also listed above.
- Use the existing portal fleet analytics for tenant state, license, module
  adoption, backup inventory, and bed census; add filters and alerting only
  after the metric definitions and data-source freshness are verified.
- Add local scheduled backup/restore drills, health checks, log rotation,
  resource limits, and operator runbooks in Compose. Keep at least one backup
  copy outside the host for real deployments; a local Docker volume alone is
  not disaster recovery.

Durable migration queues, managed database failover, multi-instance load
balancing, centralized cloud secrets, off-host object storage, and external
observability are useful later steps but require separate service/operational
costs. Review `docs/ONBOARDING_IMPROVEMENTS.md` for the prioritized backlog.
