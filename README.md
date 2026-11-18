# Hindi Content Factory

This is a clean rebuild. It does not import or execute the quarantined n8n
workflow collection in `..\content engine\QUARANTINE_UNTRUSTED_2026-10-17`.

The current profile is Hindi, faceless, dark-cinematic Instagram Reels. The
factory starts paused and uses mock publishers; every Reel remains approval-
gated before publication.

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
