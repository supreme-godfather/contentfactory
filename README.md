# Hindi Content Factory

This is a clean rebuild. It does not import or execute the quarantined n8n
workflow collection in `..\content engine\QUARANTINE_UNTRUSTED_2026-10-17`.

The current profile is Hindi, faceless, dark-cinematic Instagram Reels. The
factory starts paused and uses mock publishers; every Reel remains approval-
gated before publication.

> The proposed completion roadmap is in
> [Completion plan v2 draft for Copilot review](#completion-plan-v2-draft-for-copilot-review-2026-11-21).
> It records the current owner goal and research review, but authorizes no
> implementation, deployment, paid purchase, or live publication by itself.

## Safe start

1. Copy `.env.example` to `.env`.
2. Keep `APP_PAUSED=true`, `MOCK_PUBLISHING=true`,
   `INSTAGRAM_ENABLED=false`, and automation disabled.
3. Install the pinned dependencies in an isolated virtual environment.
4. Start with `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
5. Check `GET /health` and `GET /ready`; authenticated operators can check
   `GET /api/status`.

For optional private RDP-only testing, set `TELEGRAM_MODE=polling` in `.env`.
This local arrangement receives Telegram commands through outbound HTTPS and
does not require a public webhook, domain, or open inbound port. It is separate
from the recommended Railway production deployment. Keep the API bound to
`127.0.0.1`. The RDP setup scripts are only needed if you choose to run the
API and worker locally; neither is required when using Railway.

For Railway, deploy this folder with the Railway CLI:

```text
railway login
railway status
railway up --service contentfactory
```

The linked production project is `serene-gentleness`; PostgreSQL, persistent
media storage, and safe application defaults are configured there. The app is
online at `https://contentfactory-production-511548c5.up.railway.app`.
`DEPLOYMENT_CHECKLIST.md` records current status and the GitHub auto-deploy
limitation. The Telegram bot is restricted to the configured admin's private
chat; other users and group chats are silently ignored. Never put secret
values in command lines, source, or chat.

Never place tokens in source code, workflow JSON, commits, logs, or chat.
Instagram uses Meta's official Graph API. Its access token is encrypted in the
database when entered through Telegram; rotate any credentials that may have
been exposed previously. Keep live publishing disabled until a private
staging Reel has been verified.

The same API can run through Railway's native Python deployment or the
included `Dockerfile`. Railway uses `TELEGRAM_MODE=webhook`, PostgreSQL, and
persistent media storage mounted at `/app/data`; the Railway render worker
connects outbound to that API. No RDP machine is needed. Do not run polling
mode on multiple API instances with the same Telegram bot token.

## Current behavior

- Owner IDs are required for Telegram actions.
- The app starts paused.
- Generate creates a queued content job, but does not publish.
- The Railway render worker (or optional RDP worker) renders queued jobs and
  uploads the MP4 to the API before the job becomes available for approval. It
  can be moved between hosts without changing the Railway API or Telegram bot.
- Approval is bound to the immutable content version.
- Publishing is blocked while paused, when mock mode is disabled without an
  integration, or when a job is not approved.
- Duplicate publication is prevented by an idempotency key.
- Pause/resume state is persisted in the database.
- A real worker-rendered MP4 and quality gate are required before publication.
- The render worker uploads rendered MP4 files to the API before approval, so
  the API never depends on a Windows-local file path.
- The Telegram webhook requires a valid `TELEGRAM_WEBHOOK_SECRET`; private
  polling worker does not use a webhook secret.
- Telegram control buttons and messages are English-only by default; generated
  channel content can remain Hindi.
- The command center is mobile-first. The Controls panel holds the
  factory switch, provider setup, and Instagram controls; the Automation
  panel shows the live on/off state and lets the owner run a cycle or toggle
  automation without redeploying. `/start`, `/status`,
  `/queue`, `/generate`, `/approve`, `/publish`, `/pause`, `/resume`,
  `/analytics`, `/automation`, `/retry`, and `/sync` are owner-protected
  Telegram commands.
- `/providers` opens the encrypted provider setup panel. It supports a Google
  service-account JSON credential, OpenAI API key, Google AI Studio key,
  Hugging Face user access token, or ElevenLabs API key; the bot deletes the
  credential message after receipt and never echoes the secret. An API key
  is only used for the provider integrations that are actually wired.
- Google/OpenAI image and voice generation automatically tries the other
  configured wired provider when an API explicitly reports exhausted quota,
  billing limits, or rate limiting. A limited provider is temporarily skipped
  (30 minutes by default, or its `Retry-After` value; confirmed quota exhaustion
  is cooled down for 12 hours). Failover can incur charges with the backup
  provider. The factory cannot query a reliable remaining-credit balance;
  it reacts to provider errors instead. OpenAI script failures use the built-in
  Hindi template.
- AI media generation is opt-in. With `MEDIA_GENERATION_ENABLED=true`,
  the API creates a Hindi Google Cloud Text-to-Speech MP3 and a Vertex AI
  Imagen scene image; the outbound render worker downloads only those job assets
  and combines them with timed captions, a slow camera move, and a
  dark-cinematic grade into the MP4. This uses generated still-image artwork,
  not AI-generated video footage. Only use original or properly licensed
  music; none is bundled.
- For topic-specific multiscene artwork, select **FLUX.1 Schnell (Hugging Face
  GPU endpoint)** under Controls → Providers → Image, add the endpoint's
  Hugging Face token, and set its base endpoint URL in the Image panel. The API
  generates six storyboard-specific frames; the Railway worker renders them
  into the video. The endpoint must be created and the model's access terms
  accepted in Hugging Face first. GPU hosting is billed by Hugging Face (or its
  cloud host), separately from Railway; enable scale-to-zero and keep no-paid
  mode OFF only when you intend to run it. Missing credentials fail the job
  explicitly rather than silently using generic fallback cards.

## Recommended: Railway rendering (no RDP required)

The API, Telegram bot, PostgreSQL, and render worker can all run in Railway;
an RDP is not required. The `factory-worker` service runs FFmpeg in its own
container and calls the API over HTTPS. Its container installs FFmpeg and
Hindi-capable fonts, runs as an unprivileged user, downloads only the assets
for its claimed job, uploads the finished MP4, then removes that job's
temporary local files. It does not need a Railway volume, domain, exposed
port, Telegram token, or database credentials.

To enable Railway rendering:

1. In `serene-gentleness` → production, add a new empty service named
   `factory-worker`. If creating it with the Railway CLI from this linked
   project, run:

   ```powershell
   railway add --service factory-worker
   railway up .\worker --path-as-root --service factory-worker --environment production --project 75733d46-5731-49e9-b47e-cf870f606127
   ```

   For Git-based deployment, connect the same
   `coderdoom-exe/contentfactory` repository after granting Railway GitHub App
   access. Set the service's **Root Directory** to `/worker`.
2. Select the included `worker/Dockerfile` as its builder. The worker folder
   contains its own pinned `requirements.txt`; the API's Railway
   configuration is not used.
3. Configure these service variables:

   ```text
   FACTORY_URL=https://contentfactory-production-511548c5.up.railway.app
   WORKER_TOKEN=${{contentfactory.WORKER_TOKEN}}
   WORKER_ID=railway-renderer
   WORKER_POLL_SECONDS=30
   WORKER_OUTPUT_DIR=/tmp/factory-media
   ```

   Add `WORKER_TOKEN` as a Railway reference to the existing API service
   variable; never paste its value into source, a command, or chat.
4. Run exactly one worker replica. Do not configure a health-check HTTP port
   for this process-only service. Keep restart-on-failure enabled.
5. In Telegram → Controls → Worker & API health, confirm
   `railway-renderer` is online. In Controls → Providers → Video renderer,
   confirm **Railway renderer only** is selected.

This is a continuously running worker, so it consumes Railway compute while
idle. It is required for rendering: RDP workers are not allowed to claim jobs.
The API's render concurrency limit prevents more than one live render lease.
The factory remains paused/mock-only until an owner resumes it, and real social
publishing still needs provider credentials and explicit approval.

## Automation engine

The factory can now turn top trends into generated jobs automatically —
but it is **off by default** and never publishes on its own:

- New Telegram and API-generated jobs use the destination selected in
  **Controls → Publishing destinations**: Instagram only, YouTube only, or
  both. Automated jobs use the same setting. The default remains Instagram to
  preserve the existing profile. In both mode, one rendered video is uploaded
  to each platform with platform-specific metadata and covers. Uploads run one
  after the other, and each counts toward the daily publication limit.
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
  and sufficient originality, creates a job for the selected destination and generates its
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
`INSTAGRAM_ACCESS_TOKEN` are configured. The account ID and token can also be
entered from Telegram's Instagram setup panel; the token is encrypted and
never echoed.
Important limitation: the Graph API fetches the video itself, so it
requires a **publicly accessible video URL**. Completed renders are exposed
through the allowlisted `/media/{job_id}.mp4` route when `PUBLIC_BASE_URL` is
configured. Only approved or published jobs are served.

## Connecting Instagram and YouTube

- **Instagram:** (1) Switch to a Professional account and link a Facebook
  Page. (2) In Meta for Developers, create an app, add Instagram Graph API
  publishing access (`instagram_basic`, `instagram_content_publish`, and
  `pages_read_engagement`), and obtain a long-lived user token for the account.
  (3) In Telegram, open **Controls → Channel accounts → Instagram setup
  steps** and follow the six short pages. The bot links to Meta and Graph API
  Explorer, then prompts for the numeric account ID and a short-lived user
  token. Add the Meta app ID/secret as `META_APP_ID` and `META_APP_SECRET` in
  Railway; the bot exchanges the short-lived token with Meta and stores the
  long-lived token encrypted. The submitted Telegram message is deleted.
  Then set `PUBLIC_BASE_URL` in Railway to the API's public HTTPS domain and
  redeploy. Tap **Verify Instagram account**; confirm the displayed username,
  then test privately before enabling live publishing.
- **YouTube:** (1) In Google Cloud Console, create a project, enable YouTube
  Data API v3 and YouTube Analytics API, then configure the OAuth consent
  screen. In Telegram, open **Controls → Channel accounts → YouTube setup /
  connect** and follow the five short pages. The bot displays the exact
  callback URL for the **Web application** OAuth client. Add the Client ID,
  Client secret, and callback URL to Railway Variables and redeploy. Return to
  Telegram, tap **Connect YouTube**, sign in as the channel owner, and approve
  access. The callback page tells you to verify the channel in the bot.

The Telegram **Controls → Instagram / YouTube setup guide** repeats these
steps. OAuth/platform setup does not enable posting: mock mode, platform
enablement, a rendered video, and explicit owner approval remain separate
gates.

Open **Controls → Channel accounts** to see connection and publishing status,
verify the actual YouTube channel name or Instagram username, replace Instagram
credentials, reconnect YouTube, enable/disable publishing, or disconnect either
account. The guided setup uses short pages with next/previous buttons. The bot
manages one account per platform; a confirmation prevents accidental
disconnection. Instagram credentials entered as Railway variables must also be
removed there and revoked in Meta to fully disconnect.

## Automatic finishing and platform uploads

Each generated job gets a topic-based title, platform-specific description
and caption, niche/topic hashtags, and YouTube tags. The FFmpeg render worker
creates a horizontal YouTube thumbnail and a vertical Instagram Reel cover
from the finished video, then uploads both alongside the MP4. Use **Quality
check** in Telegram to preview the cover and review the complete publishing
package before approving.

For live YouTube publishing, the API uploads the title, description, and tags,
waits for video processing, and sets the generated custom thumbnail. For
Instagram, it includes the generated cover URL and complete hashtag caption in
the Reels container. Instagram and YouTube have different metadata/cover
capabilities; the publisher uses each platform's official API. Live uploads
remain blocked without their required thumbnail and public HTTPS media URL.
Generated metadata and visuals are automated, but review and approval remain
deliberate owner actions; the system will not publish a video on its own.

## API checklist and cost boundaries

Collect only credentials for integrations you plan to turn on:

| Workflow | API / credential | Notes |
|---|---|---|
| YouTube publishing | YouTube Data API v3 + Google OAuth Web client ID/secret | Data API upload quota is separate from billing; follow the in-bot callback URL. |
| YouTube analytics | YouTube Analytics API + the same OAuth connection | Optional; analytics does not publish videos. |
| Instagram publishing | Meta developer app + Graph API user token, app ID/secret, Instagram Professional account ID | Publishing requires Meta permissions and a linked Facebook Page. Meta may require app review for accounts outside app roles. |
| Scriptwriting | Google AI Studio Gemini API key, Hugging Face Inference Providers token, OpenAI API key, or the built-in Hindi template | Gemini app subscriptions do not pay for API calls. AI Studio quotas and any API billing are separate. Hugging Face availability and pricing depend on the selected inference provider/model. Script generation falls back to the template on provider errors. |
| Narration and images | Google Cloud service-account JSON, project ID, and Vertex AI access; OpenAI API key; ElevenLabs API key; or online Edge TTS for voice | Usage-billed services may incur charges. Configure a cloud budget alert and spend limit where available before enabling. |
| Video rendering | FFmpeg on the Railway worker only | No video-generation API is connected; the worker assembles the script, artwork, voice, captions, and covers. Railway worker compute is billed while running. RDP workers cannot claim production jobs. |

Gemini is connected for scriptwriting only; it does not accelerate FFmpeg
rendering. The engine does not connect Kling, Runway, Higgsfield, fal.ai, or an
external trend-search service. Do not spend time creating those keys until
their integrations are implemented. AI Studio free-tier availability and
quotas can change; use a Google AI Studio project with billing disabled if
you want to avoid accidental paid Gemini usage.
Official references: [Meta Graph API publishing](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media),
[YouTube quota costs](https://developers.google.com/youtube/data/determine_quota_cost),
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), and
[OpenAI API pricing](https://developers.openai.com/api/docs/pricing).

## Encrypted backup and Railway account migration

Railway's built-in volume backups are useful for same-project recovery, but
Railway documents that they restore only to the same project and environment.
For a portable copy, run `.\backup-factory.ps1` from this project on the RDP or
PC while the old Railway service is still available. It asks for the factory
HTTPS URL, the admin API token (hidden), and a passphrase; it streams the
database and `/app/data/media` into a local AES-256-GCM encrypted `.hcfb` file.
Use a unique passphrase of at least 16 characters and keep it separately from
the archive. Losing it makes the backup unrecoverable. The old app's export
endpoint is authenticated and is read-only.

Before moving accounts, preserve these Railway variables in a password
manager: `TOKEN_ENCRYPTION_KEY` (must stay exactly the same to decrypt stored
provider/Instagram/YouTube credentials), `ADMIN_API_TOKEN`, `WORKER_TOKEN`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_OWNER_IDS`, Google/OpenAI credentials,
`YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `META_APP_ID`, and
`META_APP_SECRET`. The encrypted archive contains the app database and media,
not the Railway environment variables or the encryption key.

For restore, create the new Railway API, an empty PostgreSQL service, and a
persistent `/app/data` volume; deploy the same factory version first, keep the
factory paused/mock-only, and set the original encryption key and required
credentials. Set `BACKUP_RESTORE_ENABLED=true` temporarily on the new API, run
`.\backup-factory.ps1`, select restore, and follow its confirmation. Restore
is rejected unless the target database and media directory are empty. Turn
`BACKUP_RESTORE_ENABLED` off immediately afterward. Then set the new
`PUBLIC_BASE_URL`, update the YouTube OAuth redirect URL in Google Cloud, and
verify the Telegram webhook, channel connections, jobs, and media before
resuming anything.

Keep the old Railway project until the new service is verified. A generated
`*.railway.app` domain may change; a custom domain or domain registered through
Railway must be reattached/repointed before closing the old account. Do not
assume a Railway-registered domain transfers automatically. Do not create a
new account solely to reset trial credits; use an account move only where it
complies with Railway's billing and account rules.

## Hindi cinematic Reel profile

The default Telegram format is a 45–60 second Hindi Instagram Reel:
vertical 9:16 output, faceless dark-cinematic visuals, high-contrast
burned-in captions, measured narration timing, and psychology/mindset/history
metadata. Automation remains off by default and every generated Reel requires
explicit Telegram approval before publishing.

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
- **🧩 Providers** — choose script, image, and narration providers
  independently in Telegram **Controls → Providers**. Script choices include
  Gemini, OpenAI, or the built-in Hindi template; images and narration can use
  their connected Google/OpenAI routes, and narration also supports Edge TTS
  and ElevenLabs. Credentials are encrypted. Higgsfield, Kling, Runway,
  fal.ai, and custom provider credentials are not usable until an integration
  is implemented.
- **🎞 Video rendering** — render jobs are pinned to the Railway FFmpeg worker;
  RDP workers cannot claim jobs. Jobs remain queued while `railway-renderer` is
  offline. This selects the compute worker, not an AI video-generation API; no
  external video-generation API is connected.

## Format packs and story series (2026-11-22)

The engine can now produce a **format pack** — a complete, versioned definition
of what a reel is — and tell **one story in six parts**.

### Packs (`app/packs.py`)

A pack carries the writer directives, the visual anchor, the hook grammar, the
beat map, the dialogue style, the caption treatment, the title formula and the
hashtag pool. The six legacy niches are kept as packs so nothing that already
worked changed. Two packs are new and series-ready:

- **`cinematic_boss_series`** — a recurring **original** cinematic character
  (an anthropomorphic panther boss) in a multi-part story: a dramatic entrance,
  rising stakes and a hard cliffhanger every part. The character is locked with
  a reference sheet that MiniMax generates once per series and that becomes the
  **first frame of every shot**, so the same character appears in every scene.
  The format follows the viral cinematic-boss wave without copying any existing
  creator's character.
- **`desi_boss_monologue`** — the script-only version: one narrator, contrast
  humour, exaggerated money lines, no character image needed. Runs on the
  current engine and costs the least per part.

Telegram: **Studio → 🎭 Format pack**. API: `GET /api/packs`.

### Story series (`app/series.py`)

One model call produces the **series bible** (logline, locked character,
setting, and the beats, cliffhanger and teaser of every part). The engine then
creates one job per part and releases them one at a time:

- Part 1 is queued for its script; parts 2-6 stay **held** until the part
  before them is live.
- Every part ends on the cliffhanger the plan promised — the engine restores
  the promised line if the editor pass rewrote it, and the quality gate
  blocks a part that lost it.
- Part N+1 opens with a recap of part N.
- A part cannot publish before the previous one is published.
- A series never spends more than its budget (`$5` by default), checked before
  every media generation.

The owner chooses the release cadence in Telegram: **1 part per day**,
**2 parts per day**, **I will publish each part myself**, or **the engine
picks the gap from analytics** (18-24 h depending on the channel's watch time).

Telegram: **Studio → 📚 Story series**. API: `GET /api/series`,
`POST /api/series`, `GET /api/series/{id}`, `POST /api/series/{id}/release`,
`POST /api/series/{id}/status`.

### Automation added with it

- `script_pipeline_loop` drafts queued work on its own, so a released part
  starts producing without anyone touching the bot.
- `scheduled_publish_loop` publishes approved parts when their release window
  arrives (only when auto-publish is on).
- A released part is produced ahead of its window, so the audience of the
  previous part is still warm when the next one lands.

### Reasoning-model fix

MiniMax M3.1-Flash is a reasoning model: it could spend the whole token budget
thinking and return **no answer at all**, which silently degraded the engine to
template scripts and template metadata. The chat helper now strips inline
reasoning, retries with a larger budget when the model only thought, and the
metadata, hook, script and research passes use budgets that leave room for the
answer.

## Higgsfield: speaking characters and the credit vault (2026-11-22)

Higgsfield is a front door to the best video models (Kling, Seedance, Wan, SOUL)
through one asynchronous API, and it bills in **credits**. The factory now
treats it as a first-class provider.

### What was added

- **`app/higgsfield.py`** — the provider: `Key ID:Key Secret` auth, the
  submit → poll → download lifecycle, idempotency keys, and four workflows:

  | Video role | Endpoint | What it does |
  |---|---|---|
  | `higgsfield` | Wan 2.6 text-to-video | prompt only |
  | `higgsfield_seedance` | Seedance 2.0 reference-to-video | **character image + dialogue audio** → the character speaks the line |
  | `higgsfield_wan_audio` | Wan 2.6 text-to-video with `audio_url` | the dialogue audio drives the shot |
  | `higgsfield_kling` | Kling 2.6 image-to-video | native sound, no voice-over |

  References must be public URLs; the engine already serves every media file
  from `PUBLIC_BASE_URL/media/<file>`, so the character sheet and the
  narration are handed to the model directly.
- **Narration first** — the voice-over is produced before the video scenes, so
  a missing voice never costs a video and the audio reference is ready.
- **`app/credits.py` — the credit vault.** Every Higgsfield generation is
  written to the cost ledger in credits as well as dollars. The owner sets the
  starting balance, the engine tracks the spend per job and per series, a cap
  stops one series from draining the pool, and the factory refuses to spend
  when the pool is empty. Telegram: **Operations → 💳 Higgsfield credits**.
  API: `GET /api/credits`, `POST /api/credits`.
- **`app/scene_templates.py` — the scene library.** Thirteen repeatable scenes
  (boss entry, convoy, auction, palace gate, private jet chai, helipad,
  golden throne, rain walk, cutting chai, school for my son, back to the
  village, police station silence, royal baraat entry), each with camera
  language, prompt skeleton, beats and sound cue. A series assigns one
  template per part, so six parts do not look like the same scene six times.
  API: `GET /api/scene-templates`.

### Credit budget

Higgsfield's own figures: a 60-second reel costs about 240 credits as six
Kling shots, or 960 at Seedance 2.5 1080p. A 15,000-credit pool is therefore
roughly 60–150 finished reels, or a 1,200-credit pilot that leaves ~13,800 to
scale the winning format. The per-series cap (default 400) and the vault
panel keep the spend visible.

## Media quality control, preflight and health (2026-11-22)

The engine now measures what it renders and refuses to publish media it has
not verified. Three new modules carry this:

- `app/media_qc.py` — the delivery profile (resolution, frame rate, audio
  presence, integrated loudness around −14 LUFS, true peak below −1 dBFS) and
  the single place those thresholds live.
- `worker/media_qc.py` — the render worker measures the file it just produced
  with `ffprobe` and `ebur128` and posts the raw measurements to the API.
- `app/preflight.py` — the checks that run immediately before a publication
  attempt: media present, media measured and passing, thumbnail present,
  platform credential valid, public URL configured, and no duplicate topic
  published in the last 24 hours.

Flow: the worker renders → measures → posts the report to
`POST /api/worker/{job_id}/qc` → the API stores it on the job metadata → the
render is only accepted when the report has no blockers → the quality gate and
the publish preflight both read that report. A render that fails the profile
is rejected instead of being uploaded to a platform.

Operators can inspect all of this:

- `GET /api/health/pipeline` — workers, queue depth, publications today, spend
  today, provider roles and cooldowns, media QC pass rate, recent failures.
- `POST /api/admin/preflight/{job_id}` — the preflight problems, quality
  report, media QC findings, publishing package and hook options of one job.
- `GET /api/health/pipeline` is also the source of the Telegram `/health`
  command and the Operations → System health panel.

## Hook variants and performance lessons (2026-11-22)

- **Hook variants** — after the retention editor pass, a separate cheap model
  call proposes three alternative opening lines for the script. The script
  review card in Telegram lists them with 🪝 buttons, and
  `POST /api/admin/jobs/{job_id}/hook` with `{"index": 0|1|2}` applies one.
  Applying a hook replaces the first paragraph of the narration, bumps the
  content version and returns the job to script review, so the new opening is
  approved like any other version. Nothing is spent on media before approval.
- **Performance lessons** (`app/lessons.py`) — after YouTube analytics are
  synced, the engine distils watch time, engagement and the strongest video
  into short directives ("viewers leave before 12 seconds: put the payoff in
  the first line") and injects them into the next script prompt. The engine
  learns from its own output instead of repeating what did not work.
- **Provider key rotation** — the MiniMax text route now tries every
  configured key (subscription plan first, then the platform key, then the
  environment variable) and remembers the one that answers. An expired plan
  key no longer silently degrades the engine to template scripts and template
  metadata; the rotation is reported in the API logs and the verified key is
  stored encrypted.

## Next implementation stage

Hindi TTS and artwork are wired through Google Cloud or OpenAI and remain
disabled until a provider credential is configured and a private test is run.
YouTube now has token refresh and official resumable upload support, but keep
`YOUTUBE_PRIVACY_STATUS=private` and `APP_PAUSED=true` for the first test.
Only enable live YouTube publishing after a private test upload succeeds.

What remains manual by design: approving every job before publication,
enabling automation and live publishers, providing a public video URL for
Instagram, and configuring real provider/platform credentials.

## Agent Coordination & Technical Audit (Antigravity — 2026-11-15)

This section serves as a shared communication channel between Copilot and Antigravity regarding code analysis, repository alignment, and critical bug fixes.

### 1. Repository & Branch Alignment
- **Confirmed Repo:** `https://github.com/coderdoom-exe/contentfactory.git` (Git remote `origin` has been updated).
- **Local checkout:** The checkout used for this review is on `master`, with many pre-existing uncommitted changes. The earlier `channel-account-manager` branch claim was not verified from this checkout. Do not commit or push the whole working tree; verify the intended branch and stage only reviewed files.

---

### 2. Reviewed fixes (Copilot — 2026-11-15)

#### OpenAI media request
`gpt-4o-mini-tts` is a valid Speech API model at the time of this review, although
OpenAI documents its shutdown for **2027-01-06**. The media integration keeps it
for now and uses the supported `gpt-image-2.5-flare` image model. The image
request uses GPT Image size/quality parameters and reads the default base64
response. A focused mocked API test checks the outgoing request.

#### ✅ Bug 2: Made `PUBLIC_BASE_URL` Requirement Instagram-Specific (`app/services.py`)
In `publish_job()`, `PUBLIC_BASE_URL` verification and cover URL construction
are Instagram-only. YouTube uploads thumbnail bytes directly and does not
need that URL.

---

### 3. Verification & Operational Status
- **Telegram Account Manager & Setup Wizards:** Multi-step YouTube OAuth and Instagram setup guides, long-lived token exchange (`exchange_instagram_token`), and FFmpeg thumbnail generation logic in `worker/worker.py` are sound and tested.
- **Worker Concurrency & Safety:** Idempotency keys, safe error masking, and unprivileged container/RDP isolation remain preserved.
### 4. Setup issue observed in Telegram (2026-11-15)
- `YOUTUBE_CLIENT_ID is not configured` means OAuth cannot start yet. Create a
  Google OAuth **Web application** client and configure
  `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` in Railway. The app can derive
  the callback from `PUBLIC_BASE_URL`, or use the explicitly configured
  `YOUTUBE_REDIRECT_URI`. The bot now opens directly to YouTube wizard step 5
  when Connect is tapped before OAuth is configured, and Channel accounts lists
  missing Railway variable names without showing secret values.
- Instagram's status with missing ID/token is expected until the owner
  completes the in-bot Instagram guide and privately submits the numeric
  Professional Account ID and short-lived token. The guide explains the Meta
  app, permissions, token exchange, and Railway public URL.
- These changes are local until this folder is deployed to the Railway API
  service. On the currently deployed bot, use **Controls → Instagram / YouTube
  setup guide**; Connect YouTube cannot work until OAuth credentials are added
  and the service redeploys.

References checked: [OpenAI text-to-speech guide](https://developers.openai.com/api/docs/guides/text-to-speech) and [Images API create reference](https://developers.openai.com/api/reference/resources/images/methods/generate).

---

### 5. Phase 2 temporary low-cost launch goal — 2026-11-15

The owner's clarified goal is **zero or near-zero Phase 2 spend for roughly the
first 2–3 months**, then to invest if revenue is generated. This is a target,
not a provider guarantee: free quotas, availability, service terms, and model
support can change.

#### A. Hindi Voiceover (TTS) — Microsoft `edge-tts`
- **Wired:** Select **Microsoft Edge TTS (online)** under Controls → API
  settings → Voice. It uses `hi-IN-MadhurNeural` and needs no API key.
- **Limit:** This is a best-effort online route, not a guaranteed free,
  unlimited, or production-supported service. If unavailable, narration is
  skipped; it does not switch to a paid provider in no-paid mode.

#### B. ElevenLabs voice (optional; credit-based)
- **Wired:** Add an ElevenLabs API key under Controls → Providers → API
  credentials, then set the voice ID under Controls → Providers → Voice.
  Select ElevenLabs as the voice provider.
- **Cost guard:** It is blocked while no-paid mode is ON. Turning that mode
  OFF allows use of the account's credits and may incur charges. The factory
  does not read the ElevenLabs balance or enforce the plan's monthly credit
  ceiling; disable paid top-ups in the ElevenLabs account and monitor usage.
- **Student benefit:** ElevenLabs advertises one year of ElevenReader Ultra
  for eligible students, but that is the reading app—not an API credit
  entitlement. The current student page does not promise a three-month
  production TTS API grant. Confirm plan/API access and commercial-use rights
  in your account before publishing generated voice content.

#### C. Scene Artwork (Images) — Hugging Face Inference Endpoint (FLUX.1 Schnell)
- **Wired:** Select **FLUX.1 Schnell (Hugging Face GPU endpoint)** under
  Controls → Providers → Image. Add the Hugging Face access token under API
  credentials and the endpoint base URL in the Image panel.
- **Model access:** Create an Inference Endpoint for `black-forest-labs/FLUX.1-schnell`
  after accepting the model's access terms. The model is open-weight and
  Apache-2.0 licensed; the hosted GPU endpoint is not free.
- **Output:** Six distinct, storyboard-specific vertical scene images per
  video, rendered on Railway. The endpoint is called once per scene and may
  take longer during a scale-from-zero cold start.
- **Cost guard:** The image route is blocked while no-paid mode is ON. Turning
  it OFF permits external GPU charges, which are separate from Railway credits.
  Configure scale-to-zero and review the endpoint's live GPU price before use.

#### D. Hindi Scriptwriting (LLM) — Google AI Studio (`gemini-3.5-flash-lite`)
- **Cost claim:** Gemini API has a free tier for some models, but availability
  and quotas depend on model, project, region, and current account status. The
  specific “15 RPM / 1,500 requests per day” numbers are not universal.
- **Wired:** Add an AI Studio API key in Controls → API settings → API
  credentials, then choose Gemini under Script. The credential is encrypted.
- **Fallback:** The built-in Hindi template is used on quota, API, or key
  errors. A quota error cools down Gemini rather than switching to a paid API.
- **Important:** No-paid mode blocks Vertex/OpenAI routes but cannot inspect
  Google project billing. Use an AI Studio project with billing disabled if
  you need to avoid paid Gemini usage. Review Google's data terms before
  sending content.

#### Current controls
- **No-paid providers** is ON by default. It blocks Vertex AI/OpenAI/Hugging
  Face image generation and automatic paid fallback. Turning it OFF requires
  a second confirmation in Telegram and can incur charges.
- In no-paid mode, choose **Gemini** for scripts, **Microsoft Edge TTS** for
  optional narration, and **No AI image**. Gemini's free tier and Edge TTS
  service availability are not guaranteed. Paid Gemini usage remains possible
  if billing is enabled on its project.
- **AI media generation** is a separate switch. When disabled, the factory
  renders without generated scene artwork or narration. No free unlimited GPU
  image route is assumed.
- These controls do not change publishing safeguards: approval is still
  required before live publishing, and mock publishing remains the safe
  default.

References: [Gemini model details](https://aistudio.google.com/docs/models/gemini-3.5-flash-lite),
[Gemini pricing and free-tier terms](https://aistudio.google.com/docs/pricing#gemini-3.5-flash-lite),
[Google AI Studio terms](https://aistudio.google.com/docs/terms), and
[edge-tts package](https://pypi.org/project/edge-tts/),
[ElevenLabs student offers](https://elevenlabs.io/students),
[ElevenLabs TTS API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert),
and [ElevenLabs pricing](https://elevenlabs.io/pricing).

#### Copilot review — temporary launch use only, with billing safeguards
- **Pollinations:** The current official API instructions say API calls use a
  Pollinations key and refer to balance/Pollen. The older direct image URL
  shown above may be a legacy route; neither unlimited use nor a permanent
  no-key free allowance is guaranteed by the current docs. It is not wired.
  No-paid mode skips AI scene images and uses the existing FFmpeg background,
  text, and generated cover flow instead.
- **Gemini:** Free access depends on the selected model, project, region, and
  current account quota. Google says limits vary by model and are not
  guaranteed; the old “15 RPM / 1,500 requests per day” figures should not be
  treated as universal. Google also says unpaid-service content may be used to
  improve its products, so do not send secrets or private user data.
- **edge-tts:** The Python package uses Microsoft's online Edge TTS service;
  that is not the same as a documented production TTS API with a service-level
  guarantee. Treat it as an optional best-effort route only after checking
  applicable service terms and testing throttling/reliability.
- **ElevenLabs:** Student benefits include an ElevenReader offer, not an
  advertised three-month API credit grant. The API can use account credits;
  keep it blocked in no-paid mode, and verify your account's publishing rights
  and top-up settings before enabling it.
- The persisted no-paid mode is the default: unavailable low-cost routes fall
  back to a template or no-AI media, never silently to a paid API. It does not
  prevent charges from Gemini if its project has billing enabled.
- Oracle Always Free is suitable to evaluate for the API, private dashboard,
  database, and low-volume CPU rendering, but it is not guaranteed production
  capacity: Oracle documents resource limits, possible idle-instance
  reclamation, and no Always Free SLA. A small quantized open-weight LLM may
  run on CPU for scripts, but expect slower inference; it does not provide
  image generation or video rendering acceleration.

For a future Antigravity-built status site, start with a private, read-only
dashboard backed by the factory API (health, worker heartbeat, queue, recent
jobs, and per-platform publication status). Keep the API/admin token on the
server, never in browser JavaScript or local storage, and do not expose an
unauthenticated public dashboard. Keep publishing approvals in the existing
owner-controlled flow until the website's authentication and action auditing
are designed.

**Decision:** The owner approved the **temporary low-cost objective**, not an
assumption that these services are guaranteed free. Gemini and Edge TTS are
available as best-effort routes; no-charge service and quotas are not
guaranteed. Keep paid providers blocked by default; allowing them requires a
clear, separate owner opt-in.

## Completion plan v2 draft for Copilot review (2026-11-21)

### Status and purpose

This section is a **review draft**, not implementation approval. It translates
the owner's current goal, the repository audit, and the external research
report into a safe implementation order for Copilot and the owner to review.
Do not commit, deploy, enable paid generation, connect a live publisher, or
alter the five existing uncommitted video-quality files solely because this
plan exists.

### Implemented and deployed milestone (2026-11-21)

The reviewed first milestone is now implemented and deployed in Railway
project `contentfactory-pro`:

- `contentfactory` API, Telegram control plane, PostgreSQL, and persistent media
  storage are online and return healthy/readiness responses.
- `factory-worker` is online with verified FFmpeg ASS/libass, loudness,
  sidechain-ducking, and Noto Devanagari support.
- `factory-clipper` is online as a separate unprivileged, outbound-only service
  using pinned `yt-dlp`, `curl-cffi`, FFmpeg, bounded temporary storage, and the
  shared authenticated worker protocol plus per-job lease tokens.
- The full local suite passes: `250 passed`; compile checks and
  `git diff --check` pass.
- A genre library of 48 blueprints shapes the hook, visual engine and pacing.
  The owner chooses one genre, shuffles inside a category, draws at random, or
  lets the engine match the genre to the topic deterministically.
- **Auto mode** removes the owner from the production loop: the script is
  approved, media is generated, the video renders, and it publishes when the
  quality gate passes. Auto publish still obeys the daily limit, the account
  binding, the pause switch, and a per-video cost ceiling. Clip auto publish
  stays off by default because clips carry third-party source risk. Every
  automatic action is written to an audit log visible in Settings → Auto mode.
- Telegram calls retry on transient network failures, so a flaky connection no
  longer loses a message or a progress screen.
- Script quality is now a three-pass pipeline on the cheap MiniMax route: a
  retention draft (hook in the first two seconds, one curiosity loop, pattern
  interrupts every six to eight seconds, breathable Devanagari sentences), a
  strict retention editor that rewrites only what fails the checklist, and a
  metadata pass that produces titles, description, hashtags, hook type and a cover
  concept. Any failed pass degrades to the previous good state.
- The owner can supply their own script, their own recorded voice-over, and
  their own cover for a job from Telegram. A custom script rebuilds the
  storyboard and captions, bumps the content version and clears approval. A
  custom voice-over replaces provider narration for that job.
- Long work runs behind a live, editable Telegram progress screen for scripting
  and media generation.
- Every final video and generated cover carries a small semi-transparent owner
  credit in the top-right corner, switchable and editable from Settings.
- Automation researches a hookable angle and verified points for each trending
  topic before the script is written.
- Original generation now stops for owner script approval before TTS, image,
  video, music, or rendering spend. Approval is bound to the current version.
- Fish Audio, ElevenLabs, and MiniMax are selectable voice providers. Telegram
  stores encrypted credentials and voice/model IDs and offers a blind voice
  benchmark. Fish Audio TTS uses S2.1 Pro controls; optional Fish ASR supplies
  timestamps to subtitle-less clips when configured and explicitly allowed.
- The Hindi Voice Browser validates current provider catalogs. Fish defaults to
  Fish Official Aryan (`4ebf5f78ef984caba1fbf70c8803f12b`), MiniMax defaults
  to the documented `hindi_male_1_v2` system voice, and ElevenLabs requires an
  authenticated Hindi account/PVC selection instead of a fragile hardcoded ID.
- Captions use a global escaped ASS track with Devanagari shaping and karaoke
  timing. ElevenLabs provider timing is preferred over estimated cues.
- Telegram has a managed BGM/SFX catalogue. Real uploaded audio can be mixed
  with narration ducking, loudness control, fades, and limiting; synthetic pink
  noise was removed.
- Original media generation uses a hybrid six-scene package: high-cost provider
  video is reserved for hook/payoff beats and other beats use scene artwork or
  deterministic fallbacks.
- Telegram navigation is organised into Studio, Review, Insights, Operations,
  and Settings, with state-aware and version-bound job actions. Premium inline
  button icons can be configured by semantic slot with automatic Unicode
  fallback.
- The clipping engine accepts public HTTPS VOD links and bounded live links,
  creates exactly three validated non-overlapping vertical candidates, burns
  ASS captions when timing is available, generates YouTube/Instagram covers,
  and keeps every candidate behind Telegram review.
- Generic audio-energy spikes now complement transcript scoring with a
  configurable lead-in, while timed Fish transcripts enable active-word
  karaoke captions. Source ID/creator metadata is retained without persisting
  signed source URLs. These ideas were independently rewritten after auditing
  `AnIntellectualBeing/GTA-Auto-Clipper`; no repository assets or runtime model
  downloads were copied.
- Approved candidates can be promoted into the existing account-bound publish
  queue; publication remains an explicit owner action.
- Railway smoke tests passed for a public direct MP4 (three authenticated
  previews of approximately 17.4 seconds) and a bounded 90-second HLS capture
  (three 30-second previews). No paid AI provider was used in either smoke test.

Operator configuration still required before a paid quality benchmark:

- Add the Fish Audio API key and reference voice ID in Telegram, or leave Fish
  disabled.
- Confirm the ElevenLabs plan permits the intended production use.
- Upload operator-approved BGM/SFX files in Telegram Audio Library.
- Run the blind voice benchmark and select the winner before public output.
- Approval-gated live publishing is enabled for YouTube and Instagram;
  automation is off, and missing platform credentials still block API calls.
  Add/verify each account before attempting its first approved upload.

Owner goal: complete a professional, income-oriented content engine and try to
qualify at least one channel for monetisation as quickly as realistically
possible. No code or provider can guarantee monetisation, views, income, or a
one-month result.

Current resources reported by the owner:

- Railway Pro deployment with the API, PostgreSQL, Telegram bot, and FFmpeg
  worker.
- MiniMax M Plan plus approximately USD 65 of API credit.
- Approximately 6,000 Higgsfield plan credits.
- Approximately 10,000 ElevenLabs credits.
- Agent Router credits are reserved for external development/coding and are not
  connected to this factory.
- No secret, token, account cookie, OAuth code, or private key should be added
  to this file, chat, logs, command lines, or source control.

### Strategic decision

1. Build two first-class engines on one control plane: an **Original Creation
   Engine** and a **Universal Clipping Engine**. The clipping engine is not
   specific to GTA, gaming, or any one platform; it must process ordinary
   videos, long recordings, podcasts, interviews, webinars, gameplay, events,
   and configured live streams.
2. Upgrade the original Hindi pipeline first because the deployed factory
   already contains its API, provider, render, approval, and publish skeleton.
   Develop the clipping vertical slice in parallel once shared storage, account
   isolation, and job orchestration are defined.
3. Treat each agency client, brand, content stream, and destination account as
   isolated configuration. Multiple branches of work may run concurrently
   without sharing credentials, budgets, assets, approvals, or destinations.
4. Treat Instagram as a distribution and audience channel; make YouTube the
   primary monetisation target unless actual account data supports another
   decision.
5. Keep FFmpeg as the default renderer. Design a renderer interface only when
   needed; defer Remotion/Manim sidecars until the FFmpeg pipeline passes the
   release benchmark.
6. Build multi-stream support in one codebase and database. A stream is a
   client/content/account profile, not a Git branch.
7. Add installable, data-first skills after the shared original and clipping
   pipelines are reliable. A new skill should not require edits throughout the
   core engine.

### Research review: accepted, conditional, and rejected claims

The supplied Gemini report is useful but must not be copied into production as
if every estimate were a requirement.

Verified and accepted:

- YouTube currently requires 1,000 subscribers plus either 4,000 qualified
  public watch hours in 12 months or 10 million qualified Shorts views in 90
  days for full ad-revenue eligibility. Starting 2027-02-01, the entry
  thresholds for new creators become 8,000 watch hours or 20 million Shorts
  views. Existing YPP members are not moved to the new entry thresholds.
- Starting 2027-02-01, Shorts Creator Pool earnings require 10 million
  qualified Shorts views in the preceding 90 days. Missing this does not remove
  the channel from YPP or stop eligible long-form earnings.
- YouTube says channel review typically takes about one month. Do not assume
  that merely submitting an application before 2027-02-01 preserves the old
  thresholds; the safest objective is acceptance before the change.
- ElevenLabs currently lists 10,000 monthly credits on its Free plan and a
  commercial licence beginning with its paid Starter plan. The owner's exact
  plan must be verified before public or monetised use.
- MiniMax currently documents M3.1 Flash, H3 video/image models, speech 2.8,
  and usage-based pricing. Preserve the code's separate M Plan and
  pay-as-you-go credentials until the owner's console confirms which products
  each key and balance cover.
- Direct TTS timestamps, ASS/libass captions, a licensed music/SFX catalogue,
  narration ducking, measured loudness, and denser visual beats address real
  defects in the current output.
- A hybrid original-content strategy is more controllable than relying on the
  Shorts view threshold alone.

Conditional and requiring owner/account verification:

- Higgsfield web-plan credits may not equal REST API credit. Do not automate a
  browser, session cookie, CLI OAuth workaround, or MCP login on Railway until
  Higgsfield's applicable terms and the owner's plan explicitly permit it.
  The current production integration expects an official API Key ID and Key
  Secret.
- Agent Router is explicitly outside the generation provider registry; do not
  request or store its development-only keys in this factory.
- Exact provider model names, prices, and rate limits remain configuration,
  not permanent business logic. They can change without a code release.
- Third-party repositories are candidates only. Recheck their current licence,
  release, dependencies, security, and Python 3.13 compatibility before use.
- Music providers and subscription plans require a licence review for every
  target platform and account. Store proof of licence with every reusable
  track; do not assume YouTube-only or Meta-only libraries are cross-platform.
- The YouTube API field and workflow for synthetic-content disclosure must be
  confirmed against the live API schema before implementation.

Rejected or deferred assumptions:

- The clipping engine must not be designed around GTA VI, gaming, or a single
  platform. Those are content profiles on top of a generic ingest, analysis,
  edit, approval, export, and publish pipeline.
- Source permission and editorial policy are agency/operator responsibilities,
  not a hard-coded GTA policy in the core engine. The platform may optionally
  record source/provenance data and enforce a per-workspace source policy, but
  the technical pipeline must remain source-agnostic.
- Clips must not be assigned a claimed USD 0.08 total cost: that estimate omits
  source storage, transfer, transcription/vision analysis, failed jobs, review
  time, music, live-capture compute, and delivery.
- Pink noise is not background music. The current uncommitted noise-mix change
  must be reviewed or replaced, not labelled a completed audio system.
- `ffmpeg-normalize`, WhisperX, MediaPipe, Remotion, Manim, and every researched
  package are not all P0 dependencies. Prefer the smallest direct FFmpeg and
  provider implementation that passes the benchmark.
- Two simultaneous FFmpeg renders are not assumed safe. Start with one atomic,
  fairly scheduled renderer and raise concurrency only after Railway CPU,
  memory, render time, and cost measurements.
- Reported RPM, view, subscriber, and cost-per-video figures are scenarios,
  not forecasts or release criteria.

Primary sources checked for this review:

- YouTube YPP changes: <https://support.google.com/youtube/answer/12843009>
- YouTube eligibility: <https://support.google.com/youtube/answer/72851>
- ElevenLabs plans: <https://elevenlabs.io/pricing>
- MiniMax pricing: <https://platform.minimax.io/docs/pricing/overview>

### Current code baseline

The factory has a sound control/deployment skeleton, but the content path is
not yet a professional engine:

- `generate_content()` writes the script and immediately spends on assets in
  the same operation. There is no script-approval gate before generation.
- `build_script()` makes one model request and accepts any sufficiently long
  response. It has no structured fact pack, critic/revision pass, or model
  comparison.
- The storyboard always has six broad retention stages. The renderer divides
  time evenly between available assets, so the script's actual beats do not
  control cuts.
- Caption time is estimated from words at 2.3 words/second. It is not aligned
  to the generated narration.
- The worker burns captions with `drawtext`, not a timed ASS track, and requires
  exactly six provider videos or six provider images.
- ElevenLabs uses one plain `eleven_multilingual_v2` request and discards
  timestamps. Edge TTS is currently the selected production voice.
- The renderer has no licensed BGM catalogue or cue-based SFX layer. The
  uncommitted pink-noise mix is only work in progress.
- Quality checks cover missing fields and simple script structure, not media
  dimensions, loudness, A/V drift, caption integrity, source rights, cost, or
  account routing.
- `PlatformAccount` rows do not own distinct OAuth/platform credentials, and
  `ContentJob` has no immutable stream or target-account ID.
- There is no workspace/client model, object-storage abstraction, resumable
  source ingest, proxy/transcript pipeline, clip-candidate model, structured
  edit timeline, live session, rolling segment buffer, or agency usage ledger.
- The current media volume and worker protocol are designed for generated job
  assets and final renders, not long source videos or continuous live capture.
- The existing platform and provider safety gates must remain in force while
  the engine is rebuilt.

Known engineering defects should be fixed before live publication:

- `app/publishers.py` sends literal `Authorization: ******` values while
  polling YouTube processing and uploading a thumbnail; custom thumbnails fail
  even after a video upload succeeds.
- `app/config.py` contains a real Telegram owner ID as the default instead of
  failing closed when `TELEGRAM_OWNER_IDS` is unset.
- Render claims are a non-atomic select-then-update operation under PostgreSQL.
- The SQLAlchemy engine is bound at import time, so test environment overrides
  can still point tests at the developer database.
- Multi-account credentials are currently singleton records.
- Database changes use startup `ALTER TABLE` statements instead of versioned
  migrations.

### Product modes and agency scope

The completed factory is one platform with several independent job kinds:

1. **Original creation:** research, script, voice, visuals, edit, approval, and
   publication from an idea or topic.
2. **Video-to-clips:** ingest an existing short or long video, find candidate
   moments, create edited variants, approve, export, and publish.
3. **Live-to-clips:** monitor a configured live source, maintain a rolling
   segment buffer, detect or receive highlight triggers, and generate clips
   during or shortly after the stream.
4. **Long-to-short repurposing:** turn an agency client's podcast, interview,
   webinar, event, course, or long-form channel video into multiple independent
   Shorts/Reels with complete context.
5. **Campaign/batch production:** apply one approved stream profile and brand
   kit to many topics or source assets with bounded concurrency and spend.
6. **Compilation/highlight production:** combine approved candidates into a
   longer recap while retaining links to the source moments and edit decisions.

The agency hierarchy should be explicit:

```text
Agency
  -> Workspace / Client
     -> Brand kit
     -> Content stream / Campaign
     -> Source connectors and source assets
     -> Platform accounts
     -> Jobs, clip candidates, deliverables, approvals, publications
     -> Provider budget, storage quota, usage/cost ledger, analytics
```

Every job must carry immutable `workspace_id`, `stream_id`, and
`target_account_id` values. No global "current account" may decide a publish
destination after a job has been created.

### Universal source ingestion

The clipping engine should accept sources through controlled adapters instead
of embedding platform-specific downloading logic in the renderer:

- Resumable browser/API upload for local MP4, MOV, MKV, WebM, WAV, and MP3
  inputs, with size, duration, codec, and workspace quota checks.
- S3-compatible object storage import through short-lived signed object
  references. Cloudflare R2, S3, or another selected object store can hold
  source media and final deliverables outside the small Railway volume.
- Import from an operator-configured direct media URL through a hardened fetch
  service with HTTPS-only policy, DNS/IP revalidation, redirect limits, MIME
  checks, maximum bytes/duration, timeouts, and private-network blocking.
- Scheduled watch folders or approved cloud-drive connectors as later
  adapters, without placing drive credentials in the render worker.
- Operator-supplied RTMP, RTMPS, SRT, HLS, or DASH live endpoints where the
  selected connector and FFmpeg build support them.
- Official platform webhooks/APIs when a platform exposes a supported live or
  VOD integration. Connector credentials remain encrypted and workspace
  scoped.
- Manual marker, API webhook, and Telegram commands that can create a highlight
  around a timestamp even when automatic analysis is disabled.

Every imported source receives a stable source record, checksum, probe result,
duration, streams/codecs, ingest state, storage location, retention policy,
workspace, optional source/provenance notes, and error history. Duplicate
uploads should reuse analysis where allowed by the workspace policy.

Object storage is part of the product architecture, not an optional detail.
PostgreSQL stores metadata and state; source videos, proxies, audio stems,
frames, generated assets, and deliverables belong in object/media storage.
Workers should use short-lived signed access and delete temporary local files
after a job.

### Video understanding and clip intelligence

Analysis should be staged so cheap signals reduce the amount sent to expensive
models:

1. Probe media and create a low-resolution proxy plus normalized analysis
   audio.
2. Detect silence/speech, audio energy, applause/laughter where practical,
   scene boundaries, black frames, freezes, motion changes, and speaker turns.
3. Transcribe with word timing and optional diarization. Preserve the original
   language and optionally generate a translated transcript as a separate
   artifact.
4. Sample representative frames for OCR, face/speaker layout, visual changes,
   slides, gameplay/HUD layout, and optional vision-model summaries.
5. Import chat, reactions, chapter markers, manual markers, or event metadata
   when the configured source provides them.
6. Generate candidate windows only after cheap filtering, adding configurable
   pre-roll and post-roll so clips do not begin or end mid-sentence.
7. Score and rank candidates, suppress overlaps/duplicates, and preserve a
   diverse set of moments instead of returning ten versions of one event.
8. Let an editor adjust in/out points, title, transcript, layout, captions,
   commentary, and destination before final rendering.

Candidate scoring is a configurable per-stream profile, not a universal fixed
formula. Available signals should include:

- Semantic completeness: the clip has a hook, understandable context, and a
  payoff without depending on unseen material.
- Transcript interest: useful insight, surprise, conflict, humour, emotion,
  quotability, question/answer, or a client-defined keyword/topic.
- Audio excitement: energy change, speech emphasis, laughter, applause, crowd,
  or a manual/live marker.
- Visual activity: scene/motion change, slide transition, face reaction,
  gameplay event, product reveal, or camera change.
- Audience signal: chat velocity, reactions, timestamps, or operator markers
  when supplied by the source connector.
- Editability: clean boundaries, sufficient resolution, intelligible audio,
  stable subject framing, and no long dead section.
- Novelty: distance from previously delivered clips and from other candidates
  in the same source.
- Brand fit: selected language, topics, duration, tone, audience, and campaign
  objective.

Store component scores and explanations so an editor can understand why a
candidate ranked highly. Never expose a made-up "viral probability" as a fact.

### Live clipping engine

Live clipping is a durable ingest/analyse/render workflow, not one long blocking
HTTP request:

- A live session record owns connector configuration, schedule, workspace,
  stream profile, health, reconnect count, and retention policy.
- The ingest worker writes short timestamped segments and a manifest into a
  bounded rolling buffer. Segment duration and buffer length are configurable.
- Clock discontinuity, reconnect, duplicate segment, missing segment, and
  source-quality changes are recorded rather than silently corrupting timing.
- Triggers may come from manual Telegram/API markers, transcript keywords,
  audio/visual peaks, scene events, chat spikes, scheduled intervals, or a
  client webhook.
- A trigger first creates a candidate with pre-roll; final extraction waits for
  the configured post-roll. Candidate generation never stops live ingestion.
- A low-resolution watermarked proxy can be ready first for rapid review; the
  approved full-resolution deliverable renders asynchronously.
- Near-live and post-stream modes are separate service levels. Near-live aims
  for minutes, not an unsupported promise of instant output; post-stream mode
  can run deeper analysis over the full recording.
- Buffer cleanup must never remove segments still referenced by a candidate,
  render, investigation, or configured retention hold.
- Provider, storage, and render circuit breakers pause one live session or
  workspace without stopping other clients.

The first live MVP should support one operator-supplied HLS/RTMP source, manual
markers, a rolling buffer, transcript/audio/scene signals, candidate review,
and one vertical render profile. Additional connectors and fully automatic
triggers come after that vertical slice is measured.

### Clip editing and deliverables

One approved candidate may produce several independent deliverables:

- 9:16 Short/Reel with tracked or manually selected focus.
- 9:16 split layout for speaker plus content, facecam plus gameplay, or host
  plus slides.
- 9:16 blurred/background-fill layout when cropping would remove context.
- 16:9 horizontal highlight, 1:1 feed clip, and client-defined dimensions.
- Clean master, caption-burned master, caption sidecar, audio-only extract,
  thumbnail/cover, transcript, title/caption/description variants, and an edit
  decision list.
- Original-language, translated-caption, and explicitly approved dubbed
  variants that share the same source timeline.
- A/B variants that change one controlled property (hook, title card, crop,
  caption style, or CTA) while retaining a parent deliverable ID.

The edit timeline should support:

- Source ranges, speed changes, jump cuts, silence reduction, freeze/replay,
  crop/keyframes, scale/pan, picture-in-picture, overlays, and transitions.
- Active-speaker, face, manual-region, motion-saliency, center, gameplay/HUD,
  slides, and fixed split-screen reframing strategies.
- Word-timed ASS captions with client typography, brand colours, safe zones,
  speaker labels, emphasis words, and optional translation.
- Optional original intro/outro, commentary, voiceover, BGM, SFX, logo,
  watermark, progress bar, CTA, sponsor card, and disclosure.
- Audio stem selection, dialogue cleanup, original-audio ducking, music/SFX
  mixing, limiter protection, and measured final loudness.
- Client brand kits containing logos, colour tokens, fonts, caption presets,
  thumbnail templates, lower thirds, CTA rules, banned phrases, and export
  defaults.

Render decisions must be stored as structured data, not only as an FFmpeg
command, so a deliverable can be revised, reproduced, audited, or sent to a
different render backend.

### Agency operations and control surfaces

Telegram remains the first owner control plane, but every action must be scoped
to a workspace and stream. Planned controls:

- Select workspace/client, stream/campaign, source, and destination account.
- Start/stop a live session and create a manual highlight marker.
- View ingest health, rolling-buffer age, source quality, analysis progress,
  queue position, worker health, spend, storage, and provider limits.
- Review ranked clip candidates with source timecodes and score explanations.
- Adjust clip boundaries, aspect/layout preset, language, caption style,
  commentary, title, CTA, and destination.
- Approve/reject/regenerate scripts, media, clips, covers, and final
  deliverables with version-bound actions.
- Batch approve/export/schedule only within explicit workspace/account scope.
- Pause one workspace, stream, live session, provider, or destination without
  pausing the whole factory.
- Receive alerts for failed ingest, reconnect loops, budget/storage limits,
  provider errors, render failures, publish failures, and expiring approvals.

A later private web dashboard may provide timeline trimming, frame-accurate
preview, waveform/transcript navigation, candidate comparison, client review,
and analytics. Admin/provider/platform secrets stay server-side and are never
stored in browser JavaScript or local storage.

Agency-grade roles can be introduced after owner-only Telegram operation is
stable: owner, agency admin, editor, reviewer, and client approver. Every state
change needs actor, timestamp, previous/new version, workspace, and request ID.

Programmatic interfaces should include authenticated endpoints/webhooks for
source ingest, live markers, job state, candidate-ready, approval, render-ready,
publish result, and usage events. Webhooks require signatures, replay
protection, bounded retries, and a delivery log.

### Target service architecture

Start with the existing API, PostgreSQL queue, and Railway worker, but define
worker roles so services can split without rewriting the data model:

```text
Telegram / private dashboard / authenticated client API
                     |
              FastAPI control plane
                     |
       PostgreSQL state + migrations + audit log
          |          |          |          |
       ingest     analysis   generation   publish
       worker      worker      worker      worker
          |          |          |
     object storage / temporary signed media access
                     |
             FFmpeg render worker(s)
```

Initial deployment may combine ingest, analysis, generation, and render in one
process service where safe. They remain separate job kinds and resource pools:

- `ingest`: network and disk bound; resumable imports and live segments.
- `analyse`: CPU/model bound; proxy, transcript, scenes, frames, scoring.
- `generate`: external-provider bound; scripts, commentary, voice, images,
  optional video, titles, and covers.
- `render`: CPU/memory bound; deterministic timeline to media outputs.
- `publish`: network/API bound; account-asserted, idempotent delivery.

PostgreSQL remains the source of truth. Object storage holds bytes. Workers do
not keep client media or credentials after the scoped task completes. Leases,
heartbeats, retries, cancellation, dead-letter state, priority, workspace
fairness, and idempotency apply to every job kind.

### Planned data model

Versioned migrations should introduce or evolve these concepts deliberately:

- `workspaces`: client isolation, state, timezone, quotas, retention, and
  billing labels.
- `workspace_members`: later role-based access without changing job ownership.
- `brand_kits`: logos, fonts, colours, caption/layout/audio presets, and rules.
- `content_streams`: mode, niche, language, skill, schedule, provider policy,
  budget, approval policy, and destination defaults.
- `platform_accounts` plus account-scoped encrypted OAuth/platform credentials.
- `source_connectors`: encrypted workspace-scoped connector configuration.
- `source_assets`: file/stream identity, object key, checksum, probe metadata,
  transcript/analysis state, and retention.
- `live_sessions` and `source_segments`: rolling ingest state and time mapping.
- `transcripts`: language, words, speakers, timing, provider/model, and version.
- `clip_candidates`: source ranges, trigger, component scores, explanation,
  overlap group, state, and editor decisions.
- `edit_timelines`: versioned structured tracks, effects, captions, audio, and
  outputs independent of an FFmpeg command string.
- `media_assets`: generated/imported asset type, storage key, checksum,
  provenance, dimensions/duration, and reuse scope.
- `content_jobs`: immutable workspace/stream/account/job-kind binding plus the
  existing lifecycle, lease, error, schedule, and approval state.
- `approvals`: artifact type/version, decision, actor, note, and timestamp.
- `deliverables`: timeline/version, format, object key, checksum, QC result,
  parent variant, and export state.
- `publication_records`: unique job/deliverable/platform/account idempotency.
- `usage_ledger`: provider requests, tokens/chars/seconds/images, storage,
  transfer, render time, estimated/actual cost, and workspace budget impact.
- `audit_events` and `webhook_deliveries`: durable operational history.

### Operational requirements

- Workspace-level storage, provider, daily publication, live-session, render,
  and monthly cost quotas with hard circuit breakers.
- Priority plus fair scheduling so one large client or live stream cannot starve
  every other stream.
- At-least-once job execution with idempotent side effects; never assume a
  worker runs exactly once.
- Cancellation and superseding versions that stop obsolete provider polls and
  renders where possible.
- Full media checksum and version tracking from source through deliverable.
- Structured logs and metrics keyed by request, job, workspace, stream, and
  worker, with secret/transcript redaction where configured.
- Backup and restore covering PostgreSQL metadata plus object-store inventory,
  while keeping encryption keys outside backups.
- Configurable source-retention and deletion jobs, storage reconciliation, and
  orphan cleanup.
- Preview proxies for fast review; full-resolution media only when needed.
- Provider/model capability and price configuration instead of assumptions
  spread across pipeline code.
- Feature flags and per-workspace rollout so new clipping, caption, renderer,
  or auto-publish behavior can be tested on one stream first.

### Target job lifecycle

The content workflow should become explicit and resumable:

```text
topic/research
  -> drafting_script
  -> awaiting_script_approval       (no media spend yet)
  -> generating_assets
  -> awaiting_render
  -> rendering
  -> quality_check
  -> awaiting_video_approval
  -> approved
  -> scheduled/uploading
  -> published | failed | cancelled
```

Every transition must be idempotent, version-bound, auditable, retry-bounded,
and protected from stale approval. Script approval and video approval are two
different gates.

The VOD clipping lifecycle should be independent but use the same job system:

```text
source_registered
  -> ingesting -> probing -> proxy_ready
  -> transcribing / analysing
  -> candidates_ready
  -> candidate_selected / editing
  -> awaiting_clip_approval
  -> rendering -> quality_check
  -> awaiting_deliverable_approval
  -> approved -> export/scheduled/uploading
  -> delivered/published | failed | cancelled
```

The live lifecycle continues ingesting while candidate jobs branch from it:

```text
live_session_scheduled
  -> connecting -> live_buffering -> monitoring
  -> zero or many candidate/edit/render jobs
  -> reconnecting as required
  -> ending -> post_stream_analysis -> completed
```

One failed candidate must not stop its live session; one failed live source must
not stop other workspaces. Ending a session prevents new automatic candidates
but does not cancel already approved edits/renders.

The structured content package should contain:

- Stream, skill, target platform account, language, format, and cost ceiling.
- Research claims with source URLs and verification state.
- Narration beats: role, spoken text, intended emotion, emphasis, visual
  direction, callout, and optional SFX cue.
- Measured narration duration and provider timestamp data.
- Primary media assets plus an edit-beat timeline.
- Word/chunk caption timing and ASS style ID.
- BGM/SFX asset IDs plus licence records.
- Provider/model/cost provenance and AI disclosure state.
- Quality, rights, and approval results.

### Implementation phases

#### Phase 0 - Review, preserve, and measure

- Preserve the five existing uncommitted files. Review them separately; do not
  overwrite, discard, or silently commit them.
- Render and retain one current baseline video and its job metadata.
- Record render time, peak memory, output dimensions/frame rate, provider cost,
  narration duration, and known quality problems.
- Confirm the canonical branch/repository and restore or remove README
  references to absent documents deliberately.
- Exit: Copilot and the owner approve the scoped implementation order and a
  reproducible baseline exists.

#### Phase 1 - Safety and reliability foundation

- Fix the YouTube thumbnail bearer headers and add a test that verifies both
  processing polling and thumbnail upload receive `Bearer <token>`.
- Remove the hardcoded Telegram owner default and fail closed when no owner is
  configured.
- Make job claims atomic on PostgreSQL using row locking or an equivalent
  atomic update, while preserving SQLite test support.
- Isolate test databases and prevent tests from using `data/factory.db`.
- Fail startup for credential-dependent features when the encryption key is
  absent or invalid, without breaking safe health-only startup.
- Introduce versioned database migrations before adding stream/account tables.
- Rotate credentials listed in `SECURITY_ROTATION_NOTICE.md` if not already
  rotated.
- Exit: full tests pass from a clean environment; no known publishing-auth,
  ownership-default, claim-race, or test-isolation defect remains.

#### Phase 2 - Provider entitlement and benchmark gate

- Verify the ElevenLabs plan name and commercial rights. If it is Free, do not
  use its output in public monetised content; choose a paid commercial plan or
  benchmark MiniMax speech under confirmed terms.
- Verify MiniMax M Plan tier, key type, quotas, pay-as-you-go balance, and the
  exact products covered by each key. Keep the keys logically separate until
  confirmed otherwise.
- Verify whether the Higgsfield balance is web-plan credit or API balance. Do
  not put browser/session authentication on the worker.
- Choose one licensed BGM/SFX source or an owner-supplied licensed library and
  define how licence proof is stored.
- Run blind script and voice tests using the same golden prompts. Compare Hindi
  naturalness, factuality, pronunciation, emotion, latency, and real cost.
- Exit: a signed-off provider matrix identifies allowed production, fallback,
  preview-only, and blocked providers for each role.

#### Phase 3 - Structured script engine and pre-spend approval

- Split script drafting from asset generation in `app/services.py`.
- Add a structured script bundle with research claims, narration beats,
  visual/callout cues, and quality scores. Keep a readable narration text for
  existing interfaces.
- Implement bounded passes: research/outline, draft, critic/revision, and
  TTS-safe Hindi/Hinglish polish. Reuse cheaper models where quality is equal;
  do not route every pass to the most expensive model.
- Add Telegram actions to preview, edit/regenerate, approve, or reject the
  script. Any edit increments the content version and invalidates downstream
  approval/assets.
- Preserve the deterministic built-in template as the safe fallback, clearly
  labelled as fallback output.
- Exit: no image, video, TTS, music, or SFX provider is called before the owner
  approves the exact current script version.

#### Phase 4 - Directed voice, real timestamps, captions, and audio

- Add a production voice adapter that returns audio plus provider timing data,
  model/provenance, and measured duration. Use a stable voice profile and an
  owner-reviewed pronunciation dictionary for recurring Hindi/English terms.
- Convert ElevenLabs character timestamps into tested word/chunk timing. Use
  provider timestamps for original content; benchmark MiniMax STT or
  `faster-whisper` for externally recorded clipping audio later.
- Generate ASS subtitles with semantic line breaking, Noto Devanagari fonts,
  restrained active-word highlighting, and separate original/gaming safe-zone
  presets. Render through FFmpeg/libass and verify complex Devanagari text.
- Add a local, allowlisted BGM/SFX catalogue. Store source, licence, permitted
  platforms/accounts, checksum, mood, beat/cue metadata, and expiry if any.
- Replace synthetic pink noise with an actual licensed music/SFX mix. Add
  narration-driven ducking, fades, limiter protection, and measured loudness
  using FFmpeg's native filters. Select final loudness targets through device
  tests instead of treating one number as universal.
- Keep Edge TTS available for previews only unless the owner deliberately
  approves a different use after reviewing its terms and quality.
- Exit: benchmark narration has no material pronunciation errors, captions
  match speech without visible drift or broken shaping, and audio passes the
  approved loudness/peak range on phone and desktop playback.

#### Phase 5 - Dynamic FFmpeg compositor and quality gate

- Separate six retention **stages** from edit **beats**. A Short may reuse six
  primary generated assets across 12-20 presentation beats through crop,
  parallax, callouts, freeze frames, transitions, and detail reveals.
- Replace the worker's exact-six/all-image-or-all-video requirement with a
  validated mixed-asset timeline. Use expensive generated video selectively
  for the hook, major reveal, or payoff; use stills and graphics elsewhere.
- Make measured audio duration drive the final timeline. Preserve variable beat
  lengths; stop dividing the entire video equally by asset count.
- Render original vertical output at an approved delivery profile (target
  1080x1920 and 30 fps unless a measured platform/provider constraint requires
  another profile). Preserve source frame rate appropriately for gameplay.
- Add a reusable hook card, callout/progress components, loop-safe ending, and
  platform-specific thumbnail/cover templates without copying another creator.
- Extend quality gates for output dimensions, codec, duration/A-V drift, audio
  stream, loudness/peak range, caption presence/safe zone, asset provenance,
  cost ceiling, provider failures, and current-version approvals.
- Exit: the golden original videos score at least 8/10 in blind human review,
  pass automated media checks, and stay within an owner-approved cost ceiling.

#### Phase 6 - Private original-content release

- Upload to a private YouTube test destination with live publishing otherwise
  disabled. Verify title, description, tags, disclosure, thumbnail, audio,
  captions, mobile crop, and processing status manually.
- Test Instagram only after the public media URL, account permissions, cover,
  caption, and private/staging workflow are verified.
- Measure actual provider spend, retries, worker time, storage, and egress; do
  not rely on the research report's per-video estimates.
- Start controlled output only after owner approval: one Short per day for the
  first quality sample, then increase to two only if the process remains
  reliable and analytics justify it. Add one original long-form video per week,
  then increase after its production quality and AVD are known.
- Exit: at least three private/staged and three public original videos complete
  end-to-end without manual file repair, credential leakage, duplicate upload,
  or a quality/account-routing failure.

#### Phase 7 - Agency workspaces, streams, accounts, and storage

- Introduce versioned `Workspace`, `BrandKit`, `ContentStream`, account-scoped
  credential, budget, and usage-ledger models. Bind jobs immutably to workspace,
  stream, and target account.
- Select object storage and implement signed upload/download plus lifecycle
  cleanup before accepting large VODs or live segments. Preserve the Railway
  volume for bounded working files, not an agency media archive.
- Scope YouTube OAuth, Instagram credentials, source connectors, provider
  policy, and reusable media to a workspace/account. The current singleton
  credential rows cannot safely serve multiple clients.
- Add a unique publication constraint on deliverable, platform, and account.
  Assert the account binding immediately before every platform API call.
- Add per-stream mode, niche, skill, language, brand kit, provider policy, cost
  cap, storage quota, retention, schedule, daily limit, circuit breaker, and
  approval policy.
- Implement atomic, priority-aware fair queue selection. Keep one render
  concurrent initially; ingest, analysis, and provider polling use independent
  limits and must not hold a render slot.
- Add Telegram workspace/stream selection with client and account names in
  every destructive, approval, export, or publish confirmation. Never rely on
  an implicit global active account.
- Exit: integration tests prove two workspaces cannot read or mutate each
  other's credentials, source media, generated assets, budgets, approvals,
  deliverables, idempotency keys, webhooks, analytics, or destinations.

#### Phase 8 - Universal VOD and live clipping engine

Implement this phase as three measured vertical slices rather than installing
every researched package at once.

**Phase 8A - VOD clipping MVP**

- Upload or import one source into object storage, probe it, make a proxy, and
  create a timed transcript.
- Detect scenes, silence/speech, audio energy, and motion using FFmpeg plus the
  smallest benchmarked dependencies. Compare MiniMax STT with
  `faster-whisper`; isolate local model dependencies from the API image.
- Generate non-overlapping candidate windows with complete sentence boundaries,
  pre/post-roll, component scores, and explanations.
- Add Telegram candidate review, manual in/out adjustment, title/hook choice,
  layout preset, caption preset, and approval.
- Render 9:16 center/manual crop, blurred fill, and one split-screen profile
  with word-timed captions and a client brand kit.
- Export a clean master, captioned master, thumbnail, transcript, metadata, and
  structured timeline. Publishing remains an explicit account-bound action.
- Exit: one long source reliably produces several distinct, editable, approved
  clips without re-uploading/re-analysing the source for every variant.

**Phase 8B - Intelligent reframing and agency workflow**

- Add active-speaker/face tracking where useful, motion-saliency and
  client-defined region tracking, facecam/content and speaker/slides layouts,
  crop keyframe editing, and HUD/safe-region profiles.
- Add diarization/speaker labels, semantic caption splitting, optional
  translation/dubbing, commentary generation, client CTA/brand templates, and
  BGM/SFX profiles.
- Add source/candidate/deliverable deduplication, batch candidate actions,
  variant parentage, client review state, signed exports, and usage/cost
  reporting.
- Support configurable source-policy modes such as record-only,
  approval-required, or workspace-blocking without hard-coding one content
  category's editorial rules into the engine.
- Exit: two isolated workspaces can process different source types and brand
  kits concurrently with no data, account, budget, or style leakage.

**Phase 8C - Live-to-clips**

- Add one operator-configured live connector, durable session state, reconnect
  handling, rolling object-storage segments, and bounded retention.
- Accept manual Telegram/API markers first, then add transcript keyword,
  audio/visual event, schedule, chat/engagement, and client-webhook triggers as
  separately testable signal adapters.
- Create candidates with configurable pre/post-roll while ingest continues.
  Produce rapid proxies for approval and full-resolution renders afterward.
- Add session health, latency, segment loss, reconnect, storage, analysis,
  budget, and candidate alerts. One failed candidate never stops ingest.
- Support near-live and post-stream analysis profiles with independent quality,
  cost, and latency targets.
- Exit: a disconnect/reconnect-tested live session can generate, review,
  approve, brand, export, and optionally publish clips while other original and
  VOD jobs continue safely.

#### Phase 9 - Installable skill system

- Introduce one validated factory-wide story/beat schema and a read-only skill
  registry.
- Start with **data-only skills**: `manifest.json`, prompts, storyboard/audio/
  caption templates, frozen style anchors, and declarative quality rules.
- Do not execute arbitrary code from a dropped skill folder. Optional Python
  adapters require a reviewed core registration point, import allowlist, no
  import-time side effects, no shell, bounded HTTP hosts/timeouts, and tests.
- A skill controls craft (narrative, visual direction, caption/audio presets);
  core controls credentials, budgets, queues, source policy, generation,
  rendering, approvals, publishing, and analytics.
- First candidates after launch: Hindi fact documentary, myth buster,
  comparison, listicle, quiz, news explainer, data/chart template, 2.5D story,
  gaming breakdown, podcast audiogram, and owner-content long-to-short.
- Exit: a new data-only skill can be installed and selected without editing
  `services.py`, `worker.py`, publishers, credentials, or database code.

#### Phase 10 - Analytics feedback and scaling

- Capture account/stream/skill/provider/model/cost for every publication.
- Sync impressions, CTR, average view duration, retention/watch time, views,
  engagement, and subscriber impact where official APIs expose them.
- Compare cohorts only after a useful sample; do not let one anomalous video
  silently rewrite strategy.
- Show recommendations and kill/continue alerts in Telegram. Keep strategy and
  publishing changes owner-approved.
- Raise worker concurrency, cadence, and provider budget only after measured
  reliability and unit economics support it.

### Release benchmark

Before public launch, render a small golden set containing original Hindi
technology/science/business topics plus operator-supplied podcast/interview,
talking-head, gameplay/event, and live-session samples. Blind-review scripts,
voices, detected clip candidates, reframes, and final edits without showing
provider names.

Hard blockers:

- Any factual error or unsourced specific claim.
- Media spend before script approval.
- Missing required provider/commercial entitlement, or a missing source-policy
  decision when that workspace is configured to require one.
- Broken Devanagari shaping, materially mistimed captions, or missing audio.
- Wrong stream/account binding or duplicate-publication risk.
- Missing current-version script/video approval.
- Missing render, thumbnail/cover, required metadata, or provider provenance.
- Output outside approved dimensions/duration/codec or material A/V drift.
- Cost over the job's hard ceiling without explicit owner approval.

Human scorecard (1-10 each):

- First-three-second hook.
- Hindi/Hinglish naturalness and pronunciation.
- Factuality and originality.
- Story escalation, payoff, and CTA.
- Visual relevance, continuity, and cut pacing.
- Caption readability and safe placement.
- Voice emotion, music fit, SFX restraint, and mix clarity.
- Overall willingness to watch, share, and watch another video.

Release threshold: no blocker, no secret/policy violation, and at least 8/10
average with no category below 7/10. Cost and render-time ceilings are set from
the measured benchmark, not research estimates.

### Thirty-day operating plan after the engine passes

- Days 1-7: provider/source decisions, baseline, P0 engineering, and golden-set
  tests. Publish nothing merely to meet a volume target.
- Days 8-14: private/staged end-to-end tests, then a controlled original Hindi
  launch only after the release benchmark passes.
- Days 15-21: evaluate hooks, viewed-versus-swiped, CTR, AVD, retention curves,
  comments, cost, and failure rate. Increase output only when quality holds.
- Days 22-30: turn winning Short topics into original 6-10 minute videos and
  use official related-video links where appropriate. Focus production on the
  best-supported niche rather than splitting equal effort across weak formats.
- In parallel, prove the Phase 8A clipping vertical slice on one representative
  agency source. Do not delay original-channel output to build every live,
  connector, tracking, translation, or multi-client feature at once.
- Aim to qualify and be accepted under YouTube's current YPP thresholds before
  2027-02-01, but do not claim that a pending application is grandfathered or
  that acceptance is guaranteed.

### Definition of complete

The project is not complete merely because it can call providers. Completion
requires all of the following:

- A professional original-content pipeline from topic through private/live
  publication, with script-first and final-video approval.
- Directed commercial-use narration, real timing, readable Hindi captions,
  licensed audio, dynamic visuals, thumbnails, and measured quality.
- Enforced provider/job/stream budgets and complete cost provenance.
- Atomic queues, bounded retries, idempotent account-bound publishing, backups,
  migrations, tests, monitoring, and no known high-severity security defect.
- At least two isolated content streams/accounts proven by tests, even if only
  the original stream is initially enabled for production.
- A universal VOD clipping pipeline that ingests once, ranks editable
  candidates, produces multiple branded deliverables, and supports explicit
  export/publication destinations.
- A reconnect-tested live pipeline with rolling segments, manual/automatic
  triggers, rapid previews, full renders, and bounded cleanup while unrelated
  jobs continue.
- Agency workspaces, brand kits, object storage, quotas, usage ledgers, audit
  events, and client/account isolation.
- A safe data-first skill contract and at least two installed skills using the
  same core pipeline.
- Analytics visible by stream/account/skill, with human-controlled strategy.
- Current deployment and recovery documentation that matches production.

### Owner decisions required before implementation

1. Current YouTube channel status: account age, subscribers, qualified watch
   hours, Shorts views, active strikes/claims, and whether two channels already
   exist. Do not post channel credentials here.
2. Which original niche should be the first benchmark: technology/science,
   business/finance psychology, educational facts, or another specific niche?
3. ElevenLabs active plan name: Free, Starter, Creator, or another paid plan.
4. MiniMax exact M Plan tier and whether the configured M Plan and Platform API
   keys are distinct in the console.
5. Higgsfield credit type: web subscription/promotion or official API balance.
6. BGM/SFX choice: owner-supplied licensed library, a cross-platform commercial
   subscription, or generated music with confirmed commercial rights.
7. Monthly hard budget for Railway plus all providers after existing credits.
8. First clipping MVP source type: podcast/interview, webinar/course, gameplay,
   event, talking-head video, another format, or a mix. Provide one non-secret
   sample file later for benchmarking.
9. First live connector: operator-supplied HLS, RTMP/RTMPS, SRT, a platform's
    official API, or another source. Also define expected concurrent live
    sessions and acceptable clip latency.
10. Agency scope for the first release: owner-only, internal editors, or client
    reviewers; expected number of workspaces, channels, and monthly source
    hours.
11. Storage preference and budget: Railway volume only for temporary files plus
    which S3-compatible object store for source/proxy/deliverable retention.
12. Initial source-policy mode: record-only metadata, mandatory operator
    approval, or a stricter per-workspace block. This is configuration, not a
    content-specific core rule.
13. Canonical Git branch and whether the existing five uncommitted
    video-quality changes should be completed, separated, or discarded after
    review.
