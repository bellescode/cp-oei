# CPOI Platform -- Vultr Deployment Runbook

**Classification:** Internal -- Managing Partner Only
**Platform:** Vultr Dedicated (US data center)
**Access:** Tailscale VPN only -- no public SSH or HTTP

---

## Prerequisites (one-time, on your local machine)

1. Install Tailscale: https://tailscale.com/download
2. Install the Infisical CLI: https://infisical.com/docs/cli/overview
3. Log in to Infisical: `infisical login`

---

## 1. Provision the Vultr Server

- Plan: at minimum 2 vCPU, 4 GB RAM, 80 GB SSD (NVMe preferred)
- OS: Ubuntu 24.04 LTS
- Region: United States (match your client base)
- **Disable public SSH in the Vultr firewall after Tailscale is configured**

---

## 2. First-login server setup

```bash
# As root on the Vultr server:

# Update and install system dependencies
apt update && apt upgrade -y
apt install -y python3.12 python3.12-venv python3.12-dev \
    libsqlcipher-dev libssl-dev nginx ufw git curl

# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
# Note the Tailscale IP and hostname -- you will use these in nginx.conf

# Install Infisical CLI
curl -1sLf 'https://dl.cloudsmith.io/public/infisical/infisical-cli/setup.deb.sh' | bash
apt install -y infisical

# Create the application user (never run the app as root)
useradd -r -m -s /sbin/nologin cpoi

# Create persistent data directories on the encrypted volume
mkdir -p /var/cpoi/data /var/cpoi/intake_files /var/cpoi/reports_output
chown -R cpoi:cpoi /var/cpoi
chmod 700 /var/cpoi/data  # database directory: owner-only

# Create the application directory
mkdir -p /opt/cpoi
```

---

## 3. Configure the firewall

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow in on tailscale0   # Allow all Tailscale traffic (port 443 from VPN)
ufw allow 41641/udp          # Tailscale UDP port
ufw enable
```

After this, port 22 (SSH) is only reachable via Tailscale. Do not open it publicly.

---

## 4. Configure Infisical

In the Infisical web console:

1. Create a project named `cpoi-production`
2. Add these secrets to the `prod` environment:

| Secret name       | Value                              |
|-------------------|------------------------------------|
| `CPOI_DB_KEY`     | A strong random string (32+ chars) |
| `SENDGRID_API_KEY`| Your SendGrid API key              |

3. Create a Machine Identity for the server. Copy the Client ID and Client Secret.

4. On the Vultr server, authenticate the Infisical CLI:
```bash
infisical login --method=universal-auth \
    --client-id=YOUR_CLIENT_ID \
    --client-secret=YOUR_CLIENT_SECRET
```

5. Edit `deploy/cpoi.service` and replace `YOUR_INFISICAL_PROJECT_ID` with your actual project ID.

---

## 5. Deploy the application

```bash
# On the Vultr server, as root:

cd /opt/cpoi
git clone <your-repo-url> .
chown -R cpoi:cpoi /opt/cpoi

# Create the virtual environment and install dependencies
sudo -u cpoi python3.12 -m venv venv
sudo -u cpoi venv/bin/pip install --upgrade pip
sudo -u cpoi venv/bin/pip install -r requirements.txt

# Run pip-audit before going live -- resolve any findings before proceeding
sudo -u cpoi venv/bin/pip install pip-audit
sudo -u cpoi venv/bin/pip-audit

# Initialize the database (this sets the encryption key from Infisical)
sudo -u cpoi infisical run \
    --projectId=YOUR_INFISICAL_PROJECT_ID \
    --env=prod \
    -- \
    venv/bin/python db/init_db.py
```

---

## 6. Configure nginx

```bash
# Replace the placeholder values in nginx.conf first:
# TAILSCALE_HOSTNAME  -- your machine's Tailscale hostname (e.g. cpoi-server.tail12345.ts.net)
# TAILSCALE_IP        -- your machine's Tailscale IP (100.x.x.x)

# Get the Tailscale TLS certificate
tailscale cert $(tailscale status --json | python3 -c "import sys,json; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))")

cp deploy/nginx.conf /etc/nginx/sites-available/cpoi
ln -s /etc/nginx/sites-available/cpoi /etc/nginx/sites-enabled/cpoi
rm -f /etc/nginx/sites-enabled/default   # disable the default site
nginx -t
systemctl reload nginx
```

---

## 7. Install and start the systemd service

```bash
cp deploy/cpoi.service /etc/systemd/system/cpoi.service
systemctl daemon-reload
systemctl enable cpoi
systemctl start cpoi
systemctl status cpoi
```

Check logs:
```bash
journalctl -u cpoi -f
```

---

## 8. Pre-go-live verification checklist

Run through every item before any real client data enters the platform:

- [ ] `systemctl status cpoi` shows `active (running)`
- [ ] Dashboard loads at `https://TAILSCALE_HOSTNAME` from your local machine via Tailscale
- [ ] `https://TAILSCALE_HOSTNAME` is not reachable from a browser not on the Tailscale network
- [ ] `pip-audit` ran with no known vulnerabilities
- [ ] `ollama` is running and `reports/narrator.py` produces real narrative output (not fallback templates)
- [ ] Send a live test alert email via the dashboard and confirm delivery to the configured recipient
- [ ] Run a full synthetic client submission through the dashboard end-to-end
- [ ] Confirm `cpoi.db` is at `/var/cpoi/data/cpoi.db` (not in the application directory)
- [ ] Confirm `intake_files/` is at `/var/cpoi/intake_files/` (not in the application directory)
- [ ] Confirm `CPOI_DB_KEY` does not appear in `journalctl`, nginx logs, or application logs
- [ ] Confirm Infisical Machine Identity is the only credential path (no `.env` file on server)

---

## 9. ollama setup (local AI model)

```bash
# Install ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model sized to your server's RAM:
#   4 GB RAM:  ollama pull llama3.2:1b
#   8 GB RAM:  ollama pull llama3.2:3b  (recommended)
#   16 GB RAM: ollama pull mistral:7b
ollama pull llama3.2:3b

# Start ollama as a service
systemctl enable ollama
systemctl start ollama

# Verify the narrator can reach it
sudo -u cpoi /opt/cpoi/venv/bin/python -c "
from reports.narrator import generate_narratives
print('ollama reachable -- narrator ready')
"
```

---

## 10. Backup procedure

Run daily via cron as the `cpoi` user:

```bash
# /etc/cron.d/cpoi-backup
0 2 * * * cpoi /opt/cpoi/deploy/backup.sh
```

`backup.sh` should:
1. Copy `/var/cpoi/data/cpoi.db` to an offsite encrypted backup (the file is already AES-256 encrypted by SQLCipher)
2. Copy `/var/cpoi/intake_files/` to the same destination
3. Write a backup_completed event to the audit log

A backup script template is not included here because the destination (S3, Backblaze, rsync target) is a configuration decision. The database file is safe to transfer as-is: it is already encrypted and cannot be opened without `CPOI_DB_KEY`.

---

## Known deferred items (v1.1 roadmap)

- Direct Jira API integration (replaces Excel for Jira-using clients)
- PostgreSQL migration (when database exceeds 500 MB)
- Client-facing portal (v2.0)
