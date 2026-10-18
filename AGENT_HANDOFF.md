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
