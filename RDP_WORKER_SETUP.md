# Legacy RDP worker setup (disabled for production)

The production pipeline is Railway-only. The production API rejects render
claims from RDP workers, so do not follow the installation steps below. The
`factory-worker` service uses `worker/Dockerfile`; see the Railway rendering
section in `README.md`. This file is retained only as a record of the former
RDP setup. Do not run `setup-rdp-local.ps1`: it creates a second API/database
and starts Telegram polling, conflicting with the production webhook.

## What you need on the RDP

- Windows 10/11 or Windows Server, with a working internet connection.
- Python 3.11+ installed with the Python Launcher (`py`).
- FFmpeg and ffprobe installed and available on `PATH`.
- The Railway `WORKER_TOKEN`, copied privately from the `contentfactory`
  service variables. Do not change or rotate it for this setup.
- A Windows user account that stays signed in. The installer starts the worker
  at sign-in; disconnecting Remote Desktop is fine, but signing out or
  shutting down stops it. Disable sleep while the machine is expected to
  render.

The worker does not need an inbound firewall rule, public IP, Railway login,
Telegram token, YouTube/Instagram credentials, or database password. It only
needs outbound HTTPS access to
`https://contentfactory-production-511548c5.up.railway.app`.

## Install the worker

1. Copy the repository's `worker` folder onto the RDP. Install Python and
   FFmpeg/ffprobe from their official sources, then open a **new** PowerShell
   window and check:

   ```powershell
   py -3 --version
   ffmpeg -version
   ffprobe -version
   ```

2. In Railway, open project `serene-gentleness` → service `contentfactory` →
   **Variables**. Copy the existing `WORKER_TOKEN` privately; do not put it in
   chat, a command, or a file you upload.

3. In PowerShell, go to the copied `worker` folder and run:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\install-rdp-worker.ps1
   ```

   The installer already targets the live Railway URL. Paste the token only
   at its hidden prompt. It stores the token under
   `%LOCALAPPDATA%\HindiContentFactory`, installs the pinned Python
   dependency, creates a sign-in Startup shortcut, and starts the worker.

4. In your private Telegram chat, open **Controls → Worker & API health**.
   The Railway API should be `ready` and the RDP worker should show `online`.
   Keep the app paused and mock-only while setting up; no live post is needed
   to check the worker heartbeat.

Run only one worker process per machine. The global render concurrency limit
is one, so a backup worker can share the same token without rendering two jobs
at once.

## If the RDP disconnects or is unavailable

If you only disconnect the Remote Desktop window while leaving the Windows
session signed in and the machine awake, the worker continues. If the machine
shuts down, loses internet, or the user signs out, the Railway bot/API stays
available but rendering pauses; jobs remain queued and an interrupted render
can be retried after its lease expires.

For rendering without the RDP, create the Railway `factory-worker` service
using `worker/Dockerfile`. Set its Root Directory to `/worker`, point
`FACTORY_URL` at the production API, reference the API's `WORKER_TOKEN`, and
set `WORKER_ID=railway-renderer`; run one replica. It uses ephemeral `/tmp`
storage and uploads each completed render to the existing API. It has no
exposed HTTP port and does not create a second API, database, Telegram bot, or
domain. Because it is always running, it consumes Railway compute even while
idle.

An always-on Oracle/Linux VM can also host the worker, but must be configured
as worker-only. Do not run `deploy-oracle-vps.sh` as an RDP backup: that script
installs another API/database and Telegram polling bot, which conflicts with
the active Railway webhook and uses separate local data.

## Group-spam protection

The bot ignores all group and supergroup messages. To prevent anyone adding
it to groups at all, open BotFather, run `/setjoingroups`, select this bot, and
disable group joining. Keep BotFather privacy mode enabled as well. In private
chats, non-owners receive one short “DO NOT DISTURB ME BETA !!” notice per
24-hour window; subsequent messages are ignored. Only the configured owner
can use the command center.

## Rendering notes

The worker outputs vertical H.264/AAC MP4s using FFmpeg; the current pipeline
uses a still image with motion effects and timed burned-in captions, not
generative video footage. Only use music you own or are licensed to use.
Keep live publishing disabled until a private staging render is reviewed.
