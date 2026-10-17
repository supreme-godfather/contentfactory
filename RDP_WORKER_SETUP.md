# RDP worker setup

The RDP worker uses an outbound HTTPS connection to Railway. Do not expose an
API port on the RDP and do not open inbound firewall rules for the worker.
Your laptop can be switched off; the RDP continues when it is powered on.

## Railway variables

Add a random `WORKER_TOKEN` to the Railway service. Use a different value from
`ADMIN_API_TOKEN`, Telegram secrets, and platform credentials.

## Windows setup

Install Python and FFmpeg on the RDP using their official installers. Then copy
the `worker` directory to the machine and run:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install httpx==0.28.1
$env:FACTORY_URL="https://supremejaat.pro"
$env:WORKER_TOKEN="<the same private Railway variable>"
.\.venv\Scripts\python.exe .\worker.py
```

Do not put the worker token in a committed file or send it in chat.

## Always-on operation

Use Windows Task Scheduler with:

- Trigger: At startup
- Run whether user is logged on or not
- Restart on failure
- Action: the venv Python executable and `worker.py`
- No inbound firewall rule

The worker only claims queued rendering jobs. It does not receive Telegram,
YouTube, Instagram, or database credentials.
