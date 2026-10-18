# Hindi Content Factory

This is a clean rebuild. It does not import or execute the quarantined n8n
workflow collection in `..\content engine\QUARANTINE_UNTRUSTED_2026-10-17`.

The first stage is deliberately paused and uses mock publishers. It provides
the durable job model and Telegram control surface before real YouTube or
Instagram credentials are added.

## Safe start

1. Copy `.env.example` to `.env`.
2. Keep `APP_PAUSED=true`, `MOCK_PUBLISHING=true`,
   `YOUTUBE_ENABLED=false`, and `INSTAGRAM_ENABLED=false`.
3. Install the pinned dependencies in an isolated virtual environment.
4. Start with `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
5. Check `GET /health`; authenticated operators can check `GET /api/status`.

For Railway, deploy this folder with the Railway CLI:

```text
railway login
railway link
railway up
```

Select the `patient-generosity` project and its production environment when
prompted. Do not use `railway variables set` for secret values copied into
chat; enter secrets directly in the Railway dashboard Variables view.

Never place tokens in source code, workflow JSON, commits, logs, or chat.
Real platform integrations stay disabled until their official OAuth setup is
completed and the credentials are stored as encrypted Railway variables.

Container deployment is intentionally deferred until a lock file with hashes
is generated and reviewed. This prevents an unreviewed dependency download
during the initial security stage.

## Current behavior

- Owner IDs are required for Telegram actions.
- The app starts paused.
- Generate creates a queued content job, but does not publish.
- The RDP worker renders queued jobs and uploads the MP4 to Railway before the
  job becomes available for approval.
- Approval is bound to the immutable content version.
- Publishing is blocked while paused, when mock mode is disabled without an
  integration, or when a job is not approved.
- Duplicate publication is prevented by an idempotency key.
- Pause/resume state is persisted in the database.
- A real worker-rendered MP4 and quality gate are required before publication.
- The RDP uploads rendered MP4 files to the API before approval, so Railway
  never depends on a Windows-local file path.
- The Telegram webhook requires `TELEGRAM_WEBHOOK_SECRET` when configured.
- Telegram control buttons and messages are English-only by default; generated
  channel content can remain Hindi.
- The command center is mobile-first: `/start`, `/status`, `/queue`, `/generate`,
  `/approve`, `/publish`, `/pause`, `/resume`, `/analytics`, and `/sync` are
  owner-protected Telegram controls.
- Google Cloud media generation is opt-in. With `GOOGLE_MEDIA_ENABLED=true`,
  Railway creates a Hindi Google Cloud Text-to-Speech MP3 and a Vertex AI
  Imagen scene image; the outbound RDP worker downloads only those job assets
  and combines them with captions into the MP4.

## Next implementation stage

Hindi TTS and Imagen artwork are wired through Google Cloud but remain disabled
until the service-account variables are configured and a private test is run.
YouTube now has token refresh and official resumable upload support, but keep
`YOUTUBE_PRIVACY_STATUS=private` and `APP_PAUSED=true` for the first test.
Only enable live YouTube publishing after a private test upload succeeds.
