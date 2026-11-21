# Railway deployment checklist

## Live deployment

| Resource | Current state |
|---|---|
| Project | `contentfactory-pro` (`50d40dd6-0ca9-40ea-8f4a-316cafb5ad51`) |
| Environment | `production` |
| API service | `contentfactory` (`805b3546-207d-47f9-bd3c-d5a792b4c937`) |
| Render service | `factory-worker` (`4366f29d-0e8e-4a3c-abf7-9ee6517dc495`) |
| Clipping service | `factory-clipper` (`a9e8e0d4-4fbc-4287-94c4-62a48b90823a`) |
| PostgreSQL | `Postgres`, online, with its persistent volume |
| API media/data volume | 500 MB at `/app/data` |
| API domain | `https://contentfactory-production-68a252bd.up.railway.app` |

The API is online. `GET /health` and `GET /ready` returned HTTP 200. The
Telegram webhook was registered at
`https://contentfactory-production-68a252bd.up.railway.app/telegram/webhook`.
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
MOCK_PUBLISHING=false
AUTOMATION_ENABLED=false
INSTAGRAM_ENABLED=true
YOUTUBE_ENABLED=true
```

The API is unpaused and approval-gated live publishing is enabled. Automation
remains off. A platform call still blocks until that platform has valid
credentials/account configuration, a complete media/thumbnail package, and an
explicit current-version owner approval.

## Source deployment

The source repository is `supreme-godfather/contentfactory`. Git-based
auto-deploy is not relied upon for this deployment. The Railway CLI deploys the
reviewed local `factory` folder to the selected service. Do not push, commit, or
change remotes as an incidental part of a Railway deployment.

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

## Clipping worker

The `factory-clipper` service is deployed as one process replica. It has no
domain or persistent volume and receives a Railway reference to the API's
`WORKER_TOKEN`. It accepts only bounded public HTTPS VOD/live jobs issued by the
authenticated API, stores work in a unique temporary directory, uploads final
candidate clips/covers, and deletes source files afterward. It never receives
Telegram, platform OAuth, database, or Fish Audio credentials.

Verified smoke tests:

- Direct public MP4: 3 vertical candidates completed and previews returned 200.
- Bounded public HLS capture: 90 seconds captured and split into 3 candidates.
- Runtime startup verifies pinned yt-dlp, FFmpeg, ASS caption support, and Noto
  fonts before polling.

## Cost and data safety

The API and PostgreSQL services each have persistent storage. Back up
PostgreSQL and media before changing services. Do not delete the project or
either volume to troubleshoot a deployment. Confirm Railway billing and set
a spending limit in the Railway dashboard.
