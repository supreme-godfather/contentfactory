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
PowerShell as Administrator in that directory, and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-rdp-worker.ps1
```

The installer asks for the worker token locally, stores it in an ACL-protected
`C:\ProgramData\HindiContentFactory\worker-token.txt`, installs the pinned
HTTP client, and creates a SYSTEM startup task. The token is not sent to
Railway in a command line or committed file.

## Always-on operation

The installer creates the Windows Task Scheduler entry automatically:

- Task: `Hindi Content Factory Worker`
- Trigger: At startup
- Account: SYSTEM
- Restart on failure
- No inbound firewall rule

The current worker produces a render manifest, not a finished MP4. It only
claims queued rendering jobs and does not receive Telegram, YouTube, Instagram,
or database credentials. FFmpeg/TTS rendering will replace the manifest stage
before real publishing is enabled.
