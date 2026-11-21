# Oracle VPS setup

This deployment runs the API, Telegram bot, and FFmpeg renderer together on
one Ubuntu VPS. Telegram uses outbound long polling, and the renderer talks
to the API over localhost. No public application port, webhook, domain,
Railway account, RDP, or database service is required for this setup.

The installer supports **Ubuntu 24.04 LTS** and is intentionally conservative:
the factory starts paused, automation is off, mock publishing is on, and
YouTube and Instagram are disabled. Keep it this way until you've reviewed a
test render and deliberately configured publishing.

## 1. Create the VPS

In the Oracle Cloud Console:

1. Choose your **home region** and create a VCN with internet connectivity if
   you do not already have one.
2. Create an Ubuntu 24.04 instance. The Always Free Ampere A1 shape can offer
   up to 2 OCPUs and 12 GB RAM, subject to your tenancy limits and regional
   capacity. Always Free resources can be reclaimed if Oracle determines the
   instance is idle. Check the current Oracle limits and prices before choosing
   a paid shape or upgrading your account.
3. Assign a public IPv4 address if you need to connect directly from your
   laptop. Add an ingress rule for **TCP 22 from your current public IP
   address only**. Do not add public rules for ports 8000 or 3389.
4. Upload your SSH **public** key when creating the instance. Never upload or
   share your private key.

Oracle's [instance creation guide](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm)
and [Always Free resource limits](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
describe the available shapes and networking.

## 2. Copy the factory package to the VPS

On your Windows laptop, create a clean ZIP of the `factory` folder, excluding
`.env`, databases, media, virtual environments, caches, and the quarantined
untrusted repository. Transfer it using SCP over SSH, for example:

```powershell
scp .\factory.zip ubuntu@YOUR_VPS_PUBLIC_IP:~/factory.zip
```

Connect using the SSH key and extract the package:

```powershell
ssh -i C:\path\to\your-private-key ubuntu@YOUR_VPS_PUBLIC_IP
```

On the VPS:

```bash
mkdir -p ~/hindi-factory
unzip ~/factory.zip -d ~/hindi-factory
cd ~/hindi-factory/factory
```

If the ZIP contains the factory files directly rather than inside a `factory`
subfolder, use `cd ~/hindi-factory` instead. Confirm `app/main.py`,
`worker/worker.py`, and `requirements.txt` exist before continuing.

## 3. Install and start

Run the installer from the extracted project directory:

```bash
sudo bash deploy-oracle-vps.sh
```

Enter the Telegram BotFather token at the hidden prompt and the trusted numeric
Telegram owner ID (or comma-separated IDs) at the following prompt. Do not put
the bot token in a command, a file you upload, or a chat. The installer stores it in a
root-readable-only environment file, generates separate random API and worker
credentials, installs FFmpeg and Python dependencies, and enables two
systemd services. It does not print the token or generated credentials.

The bot should respond to `/start` for the configured owner after the API
starts. Other users and group chats remain ignored.

## Operations and recovery

```bash
sudo systemctl status hindi-factory-api hindi-factory-worker
sudo journalctl -u hindi-factory-api -u hindi-factory-worker -n 80 --no-pager
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
```

Persistent application data, SQLite database, media, and the worker token are
under `/opt/hindi-factory/data`. Back up this directory securely and protect
the VPS SSH key. Do not expose port 8000 to the public internet.

The installed app directory is `/opt/hindi-factory`; update it only after
backing up the data directory. The installer refuses to overwrite an existing
installation. If setup fails, inspect the systemd logs before retrying; do not
delete the data directory.

This private deployment can generate and render videos but cannot use
Instagram's official publishing API without a publicly accessible HTTPS video
URL. Keep live publishing disabled unless you later configure a secure public
media host and explicitly decide to enable it. Manual approval remains
required.
