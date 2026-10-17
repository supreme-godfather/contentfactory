# Security rotation required

During a diagnostic variable listing, secret values were exposed in the local
session output. Treat the following as compromised and rotate them before
using the deployment:

- Telegram BotFather token: revoke and regenerate with BotFather.
- `ADMIN_API_TOKEN`: replace in Railway Variables.
- `WORKER_TOKEN`: replace in Railway Variables and rerun the RDP installer.
- `TELEGRAM_WEBHOOK_SECRET`: replace in Railway Variables.

Do not paste replacement values into chat, logs, screenshots, or source files.
