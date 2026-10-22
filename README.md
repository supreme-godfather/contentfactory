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
- The command center is mobile-first and deliberately simple: the main
  menu has only eight buttons (Create, Queue, Status, Trends, Analytics,
  Controls, Automation, Pause/Resume). The Controls panel holds the
  factory switch, provider setup, and YouTube connection/sync; the
  Automation panel shows the live on/off state and lets the owner run a
  cycle or toggle automation without redeploying. `/start`, `/status`,
  `/queue`, `/generate`, `/approve`, `/publish`, `/pause`, `/resume`,
  `/analytics`, `/automation`, `/retry`, and `/sync` are owner-protected
  Telegram commands.
- `/providers` opens the encrypted provider setup panel. It supports a Google
  service-account JSON credential or an OpenAI API key; the bot deletes the
  credential message after receipt and never echoes the secret.
- AI media generation is opt-in. With `MEDIA_GENERATION_ENABLED=true`,
  Railway creates a Hindi Google Cloud Text-to-Speech MP3 and a Vertex AI
  Imagen scene image; the outbound RDP worker downloads only those job assets
  and combines them with captions into the MP4.

## Automation engine

The factory can now turn top trends into generated jobs automatically —
but it is **off by default** and never publishes on its own:

- **YouTube-only for now.** Automation creates YouTube jobs only; this is
  enforced in code, so `AUTOMATION_PLATFORM` values other than `youtube`
  are ignored (with a log warning) until Instagram automation is added
  later. Telegram `/generate` likewise creates YouTube jobs.
- Set `AUTOMATION_ENABLED=true` to run a background cycle every
  `AUTOMATION_INTERVAL_SECONDS` (default 300, minimum 30). Automation can
  also be turned on/off at runtime — from the Telegram 🤖 Automation
  panel or `POST /api/automation/toggle` — and that runtime choice is
  persisted and wins over the environment default. A cycle also runs
  on demand via `POST /api/automation/run`; check it with
  `GET /api/automation/status` or the Telegram `/automation` command.
- Each cycle first requeues failed jobs (up to `AUTOMATION_MAX_RETRIES`
  attempts), then takes up to `AUTOMATION_MAX_JOBS_PER_CYCLE` trend
  candidates with `total_score >= AUTOMATION_MIN_TREND_SCORE` (default 70)
  and sufficient originality, creates a YouTube job and generates its
  script/assets. The trend is marked `queued`.
- If the factory is paused (`APP_PAUSED=true` or Telegram `/pause`), the
  cycle does nothing and reports `factory_paused`.
- **Human approval is still mandatory.** Automated jobs are only created
  and generated; rendering, the quality gate, approval and publishing work
  exactly as before, and publishing stays approval-gated.

Failed jobs can be retried with `POST /api/jobs/{id}/retry` or the
Telegram `/retry <job-id>` command (or the Retry button on a job card).
Retrying resets the job to `queued` and increments its retry count.

Limits that were previously only configured are now enforced:
`MAX_DAILY_PUBLICATIONS` caps published records per UTC day,
`MAX_CONCURRENT_JOBS` caps live render leases, and worker media uploads
are rejected once `MAX_STORAGE_GB` would be exceeded.

## Instagram publishing

Instagram uses the official Meta Graph API Reels flow and stays disabled
unless `INSTAGRAM_ENABLED=true` **and** `INSTAGRAM_USER_ID` /
`INSTAGRAM_ACCESS_TOKEN` are configured (token via environment only).
Important limitation: the Graph API fetches the video itself, so it
requires a **publicly accessible video URL** — a locally rendered MP4 file
cannot be uploaded directly. Local renders are therefore blocked with an
explanation until the MP4 is hosted at a public URL.

## Scriptwriting and analytics

`generate_content` uses an AI scriptwriter when an OpenAI provider key is
configured and selected; otherwise it falls back to the built-in Hindi
template script — it never fails because of the provider.
`GET /api/analytics/recommendations` (and the Telegram Analytics view)
returns deterministic recommendations computed from stored analytics
snapshots: top platform by views, top content by engagement rate and
average engagement, with no growth guarantees.

## Retention & monetisation upgrade (2026-10-22)

Built by applying `MASTER_PROMPT.md` (the reusable prompt for building a
real, income-capable YouTube automation — copy it to any AI builder) and
the audited ideas in `EXTRACTED_ASSETS_REVIEW.md`:

- **Retention scripts** — every script (AI or template) follows the arc:
  golden hook (first 3s) → disrupt the assumption → insider facts →
  ultimate truth → discussion question → fixed closing line, at a
  ~130–150 word budget for a ~60s narration.
- **Storyboard planner** (`app/storyboard.py`) — generated metadata now
  carries a 6-scene storyboard timed from the narration itself
  (~2.3 words/second), one frozen style anchor for visual consistency,
  and caption cues (≤2 lines × 42 chars, ≥1s each) for muted playback.
- **Quality gate v2** (`app/quality.py`) — hard blockers (including a
  title over YouTube's 100-character limit) plus non-blocking warnings
  (weak hook, missing discussion CTA / hashtags / storyboard / captions /
  AI disclosure, off-target duration), shown in the API and in Telegram.
- **Monetisation tracking** — `GET /api/analytics/monetisation` and the
  Analytics recommendations report estimated progress toward the YouTube
  Partner Program's 4,000 watch hours from synced snapshots, clearly
  labelled as an estimate (YouTube Studio is the source of truth), plus a
  retention recommendation based on average seconds watched per view.

No income is guaranteed by any of this — it exists so every video the
factory makes is original, retention-shaped, policy-clean and measured.
A ready-made starting list lives in `TOPIC_BANK.md`: the first 10 scored
evergreen topics with their hook lines.

## Telegram headquarters (2026-10-22)

Telegram is the headquarters: everything the owner changes day-to-day is
set from the ⚙️ Controls panel at runtime (`app/hq.py`), persisted in the
database, applied immediately, no redeploy:

- **🎯 Niche** — presets (Hindi educational facts by default, science,
  history, money, motivation). The scriptwriter and job metadata use the
  current niche for every new job.
- **🎞 Format** — Shorts (vertical 9:16, ~60s, 130–150-word scripts) or
  Long-form (landscape 16:9, ~4 min, 550–700-word chaptered scripts).
  The worker renders the matching canvas and no longer truncates long
  videos at 90 seconds; renders include fade in/out, loudness
  normalisation and an audio fade-out.
- **📤 Daily upload limit** — 1/2/3/4/6/10 per day from Telegram; the
  publish path enforces the runtime value over the env default.
- **🧩 Providers** — any provider can be added with an API key from
  Telegram (Google, OpenAI, Higgsfield, Kling, Runway, fal.ai, or a
  custom one), stored Fernet-encrypted, and the active provider can be
  switched instantly when one runs out of credits or hits a limit.
  Google/OpenAI have generation routes today; other providers' keys are
  stored ready and media generation safely skips until their endpoint is
  connected (it never crashes a job).

## Next implementation stage

Hindi TTS and artwork are wired through Google Cloud or OpenAI and remain
disabled until a provider credential is configured and a private test is run.
YouTube now has token refresh and official resumable upload support, but keep
`YOUTUBE_PRIVACY_STATUS=private` and `APP_PAUSED=true` for the first test.
Only enable live YouTube publishing after a private test upload succeeds.

What remains manual by design: approving every job before publication,
enabling automation and live publishers, providing a public video URL for
Instagram, and configuring real provider/platform credentials.
