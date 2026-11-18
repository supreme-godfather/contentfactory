# Railway deployment checklist

## Live deployment

| Resource | Current state |
|---|---|
| Project | `serene-gentleness` (`75733d46-5731-49e9-b47e-cf870f606127`) |
| Environment | `production` |
| API service | `contentfactory` (`7aa783c0-c880-4684-a9cd-79e8d0c60b66`) |
| PostgreSQL | `Postgres`, online, with its persistent volume |
| API media/data volume | 500 MB at `/app/data` |
| API domain | `https://contentfactory-production-511548c5.up.railway.app` |

The API is online. `GET /health` and `GET /ready` returned HTTP 200. The
Telegram webhook was registered at
`https://contentfactory-production-511548c5.up.railway.app/telegram/webhook`.
The BotFather token, webhook secret, worker token, admin API token, and Fernet
encryption key are stored as Railway variables; never print them, copy them
into this file, or rotate the encryption key after credentials are stored.

The Telegram owner allowlist has one configured admin. Only that admin's
private chat can use the bot. Group traffic is ignored; non-owners receive one
short refusal message per 24-hour window, with repeat messages ignored.
Disable group joining in BotFather as an additional barrier. Keep
`TELEGRAM_OWNER_IDS` limited to trusted numeric Telegram user IDs.

## Safe operating state

The authenticated live status endpoint was checked and reported:

```text
APP_PAUSED=false
MOCK_PUBLISHING=true
AUTOMATION_ENABLED=false
INSTAGRAM_ENABLED=false
YOUTUBE_ENABLED=false
```

The API is unpaused so Railway can render jobs, but publishing remains
mock-only with both social platforms disabled. Do not enable live publishing
as a deployment test; approval is still required.

## Source deployment

The private source repository is `coderdoom-exe/contentfactory`. Railway's
GitHub integration previously could not access that private repository, so
Git-based auto-deploys are not yet verified. The Railway CLI can deploy the
current local `factory` folder to the linked `contentfactory` service. This
folder's Git remote points to a different repository; do not push its branch
or change its remote as part of a Railway deployment.

The local `configure-railway-telegram.ps1` helper targets the verified
production project/service, preserves existing secrets and the owner
allowlist, and skips deploys while setting variables. Use
`-RotateTelegramToken` only when intentionally replacing the BotFather token.
It never rotates `TOKEN_ENCRYPTION_KEY`.

## Render worker

The `factory-worker` Railway service is deployed in production and uses one
replica. Its worker token is a Railway reference to `contentfactory`'s existing
`WORKER_TOKEN`; never print or copy the secret into source, logs, or chat. The
API accepts render claims only from `railway-renderer`; RDP workers cannot
claim jobs. The always-on worker consumes Railway compute while idle.

## Cost and data safety

The API and PostgreSQL services each have persistent storage. Back up
PostgreSQL and media before changing services. Do not delete the project or
either volume to troubleshoot a deployment. Confirm Railway billing and set
a spending limit in the Railway dashboard.
