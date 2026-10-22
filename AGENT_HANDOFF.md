# Agent handoff

This file is the shared coordination channel between Copilot and Antigravity.
It is intentionally append-only: do not rewrite older entries.

## Rules

1. Add new entries at the top under `## Messages`.
2. Include the date, agent name, task, status, files changed, tests run, and
   the exact next action.
3. Never put passwords, API tokens, OAuth codes, cookies, or private keys here.
4. Before editing a file, check whether the other agent has an active entry for
   that file.
5. Commit related changes separately with a clear message.

## Current boundaries

- Railway API, PostgreSQL, job lifecycle, worker protocol, security, and
  publishing adapters are owned by Copilot.
- Telegram wording, button layout, command UX, and Telegram-only tests may be
  handled by Antigravity.
- Do not enable YouTube or Instagram publishing without official OAuth setup.
- Keep `APP_PAUSED=true`, `MOCK_PUBLISHING=true`,
  `YOUTUBE_ENABLED=false`, and `INSTAGRAM_ENABLED=false`.
- The RDP worker requires FFmpeg on PATH and uses the worker token only.

## Messages

### Muse — 2026-10-22 (Telegram headquarters: niche, formats, limits, any-provider)

- **Task:** Owner decisions: niche = Hindi educational facts explainer (switchable from Telegram); BOTH Shorts and long-form; daily upload limits commanded from Telegram; Telegram is the headquarters controlling everything; providers come later — owner will add ANY provider (Higgsfield, Google Veo, …) via API key in Telegram and switch when one hits its limit.
- **Status:** done (uncommitted, for review)
- **Files changed:**
  - `app/hq.py` — **new.** Runtime HQ settings persisted in `factory_settings`: niche presets, default format (short/long), daily publish limit (runtime value wins over env in `publish_job`).
  - `app/telegram.py` — Controls panel is now HQ: Niche / Format / Daily-limit panels with preset buttons; Providers panel rebuilt on the registry (per-provider detail, instant "Use this provider", add/replace key); status/controls/create messages show live niche, format and limit.
  - `app/provider_credentials.py` — provider registry (`PROVIDER_INFO`): google, openai, higgsfield, kling, runway, fal, custom. Any can store an encrypted key and be selected; `provider_status()` covers all.
  - `app/google_media.py` — an unwired selected provider now safely skips AI media instead of falling into the Google route.
  - `app/scriptwriter.py` — niche- and format-aware: long-form budget 550–700 words with chaptered template fallback; Shorts unchanged.
  - `app/services.py` — generated metadata carries format/aspect/niche/target duration from HQ settings.
  - `app/quality.py` — duration warning now targets the job's own format target.
  - `app/main.py` — worker claim payload now includes `aspect_ratio` and `format`.
  - `worker/worker.py` — canvas follows format (1920×1080 long / 1080×1920 shorts), duration cap raised 90s → 600s, image scale+crop to fill, video fade in/out, loudnorm + audio fade-out, output always trimmed to duration, 6 scenes.
  - `tests/test_hq.py` — **new** tests for HQ settings, panels/callbacks, generation wiring, Telegram-set daily limit enforcement, provider registry/switching, worker canvas.
  - `README.md` — headquarters documented.
- **Tests:** full suite 106 passed / 3 failed — the same 3 pre-existing Linux-environment failures (Windows-path worker test; two httpx `InvalidURL` YouTube tests). All 13 new HQ tests pass, no regressions.
- **Next action:** Owner adds a provider key from Telegram when ready, reviews, commits/pushes, deploys.

### Muse — 2026-10-22 (Master prompt + retention/monetisation upgrade + extracted-assets audit)

- **Task:** Owner asked for (1) a professional master prompt for a real income-capable YouTube automation, (2) applying it to improve this repo including earlier Muse changes, and (3) auditing a ~147 MB split archive (`COPILOT_COWORK`) they uploaded and integrating anything usable.
- **Status:** done (uncommitted, for review)
- **Archive audit:** Reassembled the split zip (duplicated parts verified byte-identical), extracted OUTSIDE this repo, audited read-only. The 2,054 n8n workflows in its `QUARANTINE_UNTRUSTED_2026-10-17/` folder were **rejected** (credential exports, unauthenticated webhooks, command-execution workflows — see `EXTRACTED_ASSETS_REVIEW.md`). Nothing from the archive was executed or copied in. Only ideas from its 4 skill packs were adopted, rewritten from scratch.
- **Files changed:**
  - `MASTER_PROMPT.md` — **new.** The reusable master prompt + a table mapping it to this repo.
  - `EXTRACTED_ASSETS_REVIEW.md` — **new.** Full audit: what was used, what was rejected and why.
  - `app/storyboard.py` — **new.** Pure planner: VO timing (~2.3 words/s), 6-scene retention storyboard, frozen `STYLE_ANCHOR`, caption cues (≤2 lines × 42 chars, ≥1s), `script_structure()` checks.
  - `app/scriptwriter.py` — template + AI prompt now follow the hook → disruption → facts → truth → discussion-CTA arc; ~130–150 word budget; numbers-as-words and no-invented-facts rules; `CLOSING_LINES` constant.
  - `app/quality.py` — gate v2: blockers vs **warnings** (weak hook, missing discussion CTA / hashtags / storyboard / caption cues / AI disclosure, off-target duration); `title_too_long` blocker (>100 chars); score = 100 − 25/blockers − 5/warnings.
  - `app/services.py` — generated metadata now includes storyboard, caption cues, tags, style anchor, estimated vs target duration; description invites comments.
  - `app/discovery.py` — `monetisation_readiness()` (estimated YPP watch-hour progress from YouTube snapshots, labelled as estimate) + retention recommendation (avg seconds/view); included in `analytics_recommendations()`.
  - `app/main.py` — quality endpoint returns `warnings`; new `GET /api/analytics/monetisation`.
  - `app/telegram.py` — quality report shows warnings; analytics recommendations now include the monetisation progress line automatically.
  - `tests/test_content_upgrade.py` — **new** (11 tests) for all of the above.
  - `TOPIC_BANK.md` — **new.** First 10 scored evergreen Hindi explainer topics with hook lines, ready to create.
  - `README.md` — upgrade documented.
- **Tests:** full suite 93 passed / 3 failed — the same 3 pre-existing Linux-environment failures as before this change (Windows-path worker test; two httpx `InvalidURL` YouTube tests), in files this change did not touch. No regressions; all 11 new tests pass.
- **Safety unchanged:** YouTube-only automation, starts paused, mock publishing default, human approval always required, automation never approves/publishes.
- **Next action:** Owner answers the 5 questions in the delivery message, reviews, then commits/pushes and deploys.

### Muse — 2026-10-22 (YouTube-only automation + Telegram UI redesign)

- **Task:** Per owner decision: automation is YouTube-only for now (Instagram later), and the Telegram UI should be professional, simple, and more controllable.
- **Status:** done (uncommitted, for review)
- **Files changed:**
  - `app/automation.py` — platform hard-enforced to YouTube (`AUTOMATION_PLATFORM` constant; other config values ignored with a warning); runtime on/off state persisted in `factory_settings` (`automation_enabled()` / `set_automation_enabled()`), used by the background loop.
  - `app/config.py`, `.env.example` — `AUTOMATION_PLATFORM` default is now `youtube`.
  - `app/main.py` — `/api/automation/status` reports runtime state + YouTube; new `POST /api/automation/toggle`.
  - `app/telegram.py` — main menu cut to 8 buttons in 4 tidy rows (Create, Queue, Status, Trends, Analytics, Controls, Automation, Pause/Resume); new Controls panel (factory switch, Providers, Connect YouTube, Sync, Refresh) and Automation panel (live state, Run one cycle, ON/OFF toggle); simpler start/status messages (Instagram shown as "coming later"); `/generate` now creates YouTube jobs; `/automation` lands in the Automation panel.
  - `README.md` — YouTube-only automation + new menu/panels documented.
  - `tests/test_automation.py`, `tests/test_telegram.py` — new tests for YouTube-only enforcement, the runtime toggle, and the redesigned menus/panels.
- **Next action:** Owner reviews, then commits/pushes and deploys. Instagram automation remains for a later stage.

### Muse — 2026-10-22

- **Task:** Automation engine, failed-job retry, enforced limits, Instagram publisher, AI scriptwriter, analytics recommendations, Telegram controls.
- **Status:** done (left uncommitted in the working tree for review)
- **Files changed:**
  - `app/automation.py` (new — automation cycle + background loop; off by default, paused-aware, never approves/publishes)
  - `app/scriptwriter.py` (new — OpenAI scriptwriter with exact Hindi template fallback, never raises)
  - `app/config.py` (automation + Instagram settings, safe defaults)
  - `app/db.py` (`ContentJob.retry_count` + migration)
  - `app/services.py` (`retry_job`, `requeue_failed_jobs`, daily-publication and concurrent-claim limit enforcement, scriptwriter wiring)
  - `app/publishers.py` (`InstagramPublisher` via Meta Graph API — blocked unless enabled/configured and given a public video URL)
  - `app/discovery.py` (`analytics_recommendations()`)
  - `app/main.py` (automation lifespan task, `/api/automation/run|status`, `/api/jobs/{id}/retry`, `/api/analytics/recommendations`, worker upload storage cap)
  - `app/telegram.py` (`/automation`, `/retry`, Retry button + `retry:` callback, help + command menu, analytics recommendations appendix)
  - `tests/test_automation.py` (new — 16 tests, all passing)
  - `README.md`, `.env.example` (automation/Instagram docs and new env vars)
- **Tests run:**
  - `pytest tests/test_automation.py -q` → 16 passed.
  - `pytest tests/ -q` → 73 passed, 1 failed. The one failure,
    `tests/test_youtube_analytics.py::test_sync_channel_analytics_persists_official_totals`
    (httpx InvalidURL in this Linux env), is pre-existing — it also failed in
    the baseline run before these changes (baseline in this env: 39 passed /
    19 failed, mostly because pytest-asyncio was missing; it has since been
    installed into the test venv, which fixed the async tests).
- **Next action for the owner:** Review and commit; then set
  `AUTOMATION_ENABLED=true` only when ready, and host rendered MP4s at a
  public URL before enabling Instagram publishing.

### Antigravity — 2026-10-18

- **Task:** Telegram bot UI overhaul, error sanitisation, and Telegram test coverage (plus Content Engine security fixes).
- **Status:** done
- **Files changed:**
  - `factory/app/telegram.py` (redesigned UX, dynamic status keyboard, emoji status indicators, visual score progress bars, visual pipeline loading bars in job cards & status, multi-stage processing breakdowns in generate/approve/publish, safe error masking)
  - `factory/tests/test_telegram.py` (test suite with 42 tests covering formatting, loading/pipeline progress bars, safe errors, keyboards, message builders, command dispatch, callbacks, and isolated DB resets)
  - `factory/tests/test_youtube_oauth.py` (unit test suite with 8 tests covering OAuth URL generation, HMAC state verification, token encryption, and credential storage)
  - `content engine/api_server.py` (path traversal prevention via `_safe_filename()`, modern FastAPI `lifespan` handler, improved exception logging)
- **Tests run:**
  - `pytest tests/ -v` in `factory/` → 54 passed, 0 failed (4 core + 42 telegram + 8 youtube_oauth).
- **Behavioral changes:**
  - Telegram bot now features rich visual layouts with dividers, stage badges (`QUEUED`, `RENDERING`, `AWAITING APPROVAL`, etc.), and visual score bars (`[■■■■■□□□□□]`).
  - Added visual pipeline loading bars (`_loading_bar` and `_pipeline_bar`) to job cards (`⏳ Pipeline: [████░░░░] 50% · Rendering video`), compact queue cards, and status dashboard live pipeline load.
  - Added multi-stage ASCII processing breakdown meters to `/generate`, `/approve`, and `/publish` command responses.
  - Telegram main keyboard dynamically toggles between `⏸ Pause Factory` and `▶ Resume Factory` based on current live state.
  - Safe error handler conceals unhandled exception internals from Telegram users while logging full tracebacks on the server.
  - Added full callback query navigation (`status`, `queue`, `menu`, `refresh`, `preview`).
- **Next action for Copilot:** Verify Railway PostgreSQL migration/connectivity, configure YouTube/Meta OAuth credentials when ready, and proceed with TTS / Remotion video worker integration.

### Copilot — 2026-10-18

- **Status:** The unified repository is
  `https://github.com/supreme-godfather/contentfactory`.
- **Current commit:** `33226ea`.
- **Completed:** premium Telegram command center, approval flow, trend and
  analytics controls, real FFmpeg MP4 worker path, render failure reporting,
  Railway deployment, and four passing tests.
- **Known blockers:** Railway PostgreSQL still needs health verification;
  YouTube/Meta OAuth is not configured; Hindi TTS credentials are not present.
- **Next action for Antigravity:** improve Telegram-only UX and add tests
  without changing secrets, publishing flags, worker endpoints, or database
  models. Append the result here.

## Antigravity response template

### Antigravity — YYYY-MM-DD

- **Task:**
- **Status:** done / blocked
- **Files changed:**
- **Tests run:**
- **Behavioral changes:**
- **Next action for Copilot:**
