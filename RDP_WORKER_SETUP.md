# RDP worker setup

The RDP worker uses an outbound HTTPS connection to Railway. Do not expose an
API port on the RDP and do not open inbound firewall rules for the worker.
Your laptop can be switched off; the RDP continues when it is powered on.

## Railway variables

Add a random `WORKER_TOKEN` to the Railway service. Use a different value from
`ADMIN_API_TOKEN`, Telegram secrets, and platform credentials.

## Windows setup

Install Python 3.11+ on the RDP using the official python.org installer and
enable the Python Launcher. Copy the `worker` directory to the machine, open
PowerShell as the signed-in RDP user in that directory, and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-rdp-worker.ps1
```

The installer asks for the worker token locally, stores it under
`%LOCALAPPDATA%\HindiContentFactory\worker-token.txt`, installs the pinned
HTTP client, and creates a Startup-folder shortcut. The token is not sent to
Railway in a command line or committed file.

## Always-on operation

The installer creates a user Startup shortcut automatically. The signed-in RDP
user session must start for the worker to run; no inbound firewall rule is
needed.

The worker renders a real vertical H.264 MP4 with FFmpeg. Install FFmpeg on
the RDP and ensure `ffmpeg.exe` is available on PATH before starting the
worker. The worker only receives a short-lived worker token; it does not
receive Telegram, YouTube, Instagram, or database credentials. Failed renders
are reported back to the API and are not marked ready for publication.

If the RDP is switched off during a render, its lease expires after the
configured `WORKER_LEASE_SECONDS` (default 15 minutes). Railway then makes the
job available for the next worker run instead of leaving it permanently stuck.
