# Cloud-only deployment

No local PC is required.

## 1. Oracle host

Create an Ubuntu LTS Ampere instance in a jurisdiction eligible for the target market. Add an SSH public key generated specifically for GitHub deployment. Run `infra/oracle/bootstrap.sh` once through Oracle Cloud Shell.

Create `/opt/sibyl-trace/.env` from `.env.example`; keep permissions at `0600`. The database, API, worker, tunnel, and backup containers are then managed by Docker Compose. Keep `AI_ANALYSIS_ENABLED=false` until an OpenAI API key with an explicit project spend limit is installed.

## 2. Cloudflare Tunnel

Create a remotely managed tunnel whose private service points to `http://api:8000`. Store its token only in Oracle `.env`. Do not expose port 8000 or PostgreSQL in the Oracle security list.

## 3. Cloudflare Access and Worker

Create a self-hosted Access application for the dashboard hostname and restrict its policy to the owner identity. Copy the application Audience (AUD) tag and team domain. The Worker validates `Cf-Access-Jwt-Assertion` on every request; missing Access configuration or an invalid token returns HTTP 403 before assets or APIs are served.

Set Worker secrets:

- `ORIGIN_BASE_URL`: tunnel hostname using HTTPS.
- `ORIGIN_SHARED_SECRET`: must equal Oracle `GATEWAY_SHARED_SECRET`.
- `ADMIN_TOKEN`: must equal Oracle `ADMIN_TOKEN`.
- `ACCESS_TEAM_DOMAIN`: `https://<team>.cloudflareaccess.com`.
- `ACCESS_POLICY_AUD`: Access application Audience tag.
- `ACCESS_OWNER_EMAIL`: exact owner login email allowed by the Access policy.

## 4. GitHub environments

Create `oracle-production` and `cloudflare-production`, both with required reviewers.

Oracle secrets:

- `ORACLE_HOST`, `ORACLE_USER`, `ORACLE_SSH_PRIVATE_KEY`, `ORACLE_HOST_KEY`.

Cloudflare secrets:

- `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.
- `ORIGIN_BASE_URL`, `ORIGIN_SHARED_SECRET`, `ADMIN_TOKEN`.
- `ACCESS_TEAM_DOMAIN`, `ACCESS_POLICY_AUD`, `ACCESS_OWNER_EMAIL`.

The Oracle workflow uploads an immutable source bundle for the selected commit and rebuilds containers remotely. The Cloudflare workflow runs a pinned Wrangler release from GitHub-hosted runners.

## 5. Optional GPT-5.6 advisory

Set `OPENAI_API_KEY` only in Oracle `.env`, choose `OPENAI_MODEL`, then set `AI_ANALYSIS_ENABLED=true`. The default model is `gpt-5.6-luna`. The system submits bounded portfolio, source-quality, signal, and paper-order evidence using pseudonymous source IDs. It requests strict structured output with `store: false`; the result is persisted for the dashboard but never enters the deterministic order-approval path.
