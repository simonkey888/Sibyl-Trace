# Cloud-only deployment

No local PC is required.

## 1. Oracle host

Create an Ubuntu LTS Ampere instance in a jurisdiction eligible for the target market. Add an SSH public key generated specifically for GitHub deployment. Run `infra/oracle/bootstrap.sh` once through Oracle Cloud Shell.

Create `/opt/sibyl-trace/.env` from `.env.example`; keep permissions at `0600`. The database, API, worker, tunnel, and backup containers are then managed by Docker Compose.

## 2. Cloudflare Tunnel

Create a tunnel whose private service points to `http://api:8000`. Store its token only in Oracle `.env`. Do not expose port 8000 or PostgreSQL in the Oracle security list.

## 3. Cloudflare Worker

Set Worker secrets:

- `ORIGIN_BASE_URL`: tunnel hostname using HTTPS.
- `ORIGIN_SHARED_SECRET`: must equal Oracle `GATEWAY_SHARED_SECRET`.
- `ADMIN_TOKEN`: must equal Oracle `ADMIN_TOKEN`.

Protect the Worker hostname with Cloudflare Access and an allow policy restricted to the owner identity.

## 4. GitHub environments

Create `oracle-production` and `cloudflare-production`, both with required reviewers.

Oracle secrets:

- `ORACLE_HOST`, `ORACLE_USER`, `ORACLE_SSH_PRIVATE_KEY`, `ORACLE_HOST_KEY`.

Cloudflare secrets:

- `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `ORIGIN_BASE_URL`, `ORIGIN_SHARED_SECRET`, `ADMIN_TOKEN`.

The Oracle workflow uploads an immutable source bundle for the selected commit and rebuilds containers remotely. The Cloudflare workflow runs Wrangler from GitHub-hosted runners.
