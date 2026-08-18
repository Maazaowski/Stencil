# Stencil — Deployment Guide (Windows Server 2025)

How to deploy Stencil on a **Windows Server 2025** machine using
**WSL2 + Docker Engine**, connecting to Temforce's **existing MySQL** instance.

> **Why not Docker Desktop?** Docker Desktop is only supported on Windows 10/11,
> not Windows Server. The Linux container stack runs under **WSL2 + Docker
> Engine (CE)** instead — same images, same `docker compose`, and no Docker
> Desktop license is involved (Docker Engine is free, open source).

---

## 1. Architecture

The stack runs as Docker containers inside a WSL2 Linux distro:

| Container | Purpose | Port |
|-----------|---------|------|
| `backend` | FastAPI API + runs DB migrations on startup | 8000 |
| `worker`  | Celery worker (the extraction pipeline) | — |
| `redis`   | Celery broker | 6379 (internal) |
| `watcher` | Watches inbound folders for new PDFs | — |
| `frontend`| Web UI (Next.js) | 3000 |

**Not** in Docker:
- **MySQL** — uses Temforce's existing instance (the bundled MySQL container is removed).
- Invoice files — live on a Windows drive, mounted into the containers.

---

## 2. Prerequisites

- Windows Server 2025 with administrator access.
- Outbound HTTPS access to `api.openai.com` from the server.
- An OpenAI API key.
- Access to the existing MySQL instance (see §4 — **details to confirm**).
- The invoice directory tree present on the server (e.g. `D:\Astera\Invoices\...`).

---

## 3. Install the runtime (WSL2 + Docker Engine)

### 3.1 Enable WSL2 and install Ubuntu
In an **administrator PowerShell**:
```powershell
wsl --install -d Ubuntu
```
Reboot if prompted. Launch **Ubuntu** once and create a Linux username/password.
Verify it is WSL **version 2**:
```powershell
wsl --list --verbose      # VERSION must be 2
```

### 3.2 Enable systemd (for clean service start)
Inside the **Ubuntu** shell:
```bash
sudo tee /etc/wsl.conf >/dev/null <<'EOF'
[boot]
systemd=true
EOF
```
Apply it from PowerShell, then re-open Ubuntu:
```powershell
wsl --shutdown
```

### 3.3 Install Docker Engine + Compose
Inside Ubuntu:
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER       # use docker without sudo
```
Close and re-open the Ubuntu shell (so the group change applies), then:
```bash
sudo systemctl enable --now docker
docker run hello-world              # sanity check — should print a success message
```

---

## 4. Existing MySQL instance

> ### ⚠️ Details to confirm with Temforce infra (Denis)
> Fill these in before continuing. Replace the `<...>` placeholders throughout
> this guide with the confirmed values.
>
> 1. **Location** — is MySQL on **this same server** or a **separate host**?
>    - Same server  → use `host.docker.internal` as `<MYSQL_HOST>`.
>    - Separate host → use its **IP/DNS name** as `<MYSQL_HOST>`.
> 2. **Host / port** → `<MYSQL_HOST>` / `<MYSQL_PORT>` (default `3306`).
> 3. **MySQL version** — confirm **8.0+** (we use `utf8mb4`).
> 4. **Can infra run the SQL below**, or should we request a DB + user be created?
> 5. **Network access** — is MySQL listening on `0.0.0.0` (or the LAN IP), and is
>    the user allowed to connect **from the Docker/WSL subnet** (host scope `'%'`
>    or a specific subnet)? Is inbound TCP `<MYSQL_PORT>` open in Windows Firewall?

### 4.1 Create the database and user
Run on the MySQL instance as an admin (adjust the password):
```sql
CREATE DATABASE stencil CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'stencil'@'%' IDENTIFIED BY '<DB_PASSWORD>';
GRANT ALL PRIVILEGES ON stencil.* TO 'stencil'@'%';
FLUSH PRIVILEGES;
```
> If infra restricts host scope, replace `'%'` with the Docker/WSL subnet, e.g.
> `'stencil'@'172.%'`. Confirm the subnet with infra.

### 4.2 Make sure it's reachable from containers
- MySQL `bind-address = 0.0.0.0` (or the host's LAN IP), not `127.0.0.1` only.
- Windows Firewall: allow **inbound TCP `<MYSQL_PORT>`** from the WSL/Docker subnet
  (the `vEthernet (WSL)` adapter).

The tables themselves are created automatically by the `backend` container on
first start (`alembic upgrade head`) — no manual schema setup needed.

---

## 5. Get the application code

Clone into the **WSL Linux filesystem** (faster builds than `/mnt/c`):
```bash
cd ~
git clone <REPO_URL> Stencil
cd Stencil
```

---

## 6. Edit `docker-compose.yml`

Make three changes:

**6.1 — Remove the bundled MySQL.** Delete the entire `mysql:` service block and
the `mysql_data` entry under the bottom `volumes:` section.

**6.2 — Drop the MySQL dependency.** In `backend`, `worker`, and `watcher`,
remove the `depends_on: mysql` block (keep the `redis` dependency).

**6.3 — Point at the existing MySQL and add host resolution.** In those same
three services set:
```yaml
    environment:
      ST_DATABASE_URL: mysql+pymysql://stencil:<DB_PASSWORD>@<MYSQL_HOST>:<MYSQL_PORT>/stencil
      ST_REDIS_URL: redis://redis:6379/0
      ST_DATA_DIR: /data
    extra_hosts:
      - "host.docker.internal:host-gateway"
```
- If MySQL is on the **same server**, `<MYSQL_HOST>` = `host.docker.internal`
  (the `extra_hosts` line makes that name resolve under Docker Engine).
- If MySQL is on a **separate host**, `<MYSQL_HOST>` = its IP/DNS name, and you
  can omit `extra_hosts`.

---

## 7. Create the `.env` file

In `~/Stencil/.env` — note Windows drives use the **WSL mount form**
(`/mnt/d/...`, not `D:\...`):
```
ST_OPENAI_API_KEY=sk-...
ST_DATABASE_URL=mysql+pymysql://stencil:<DB_PASSWORD>@<MYSQL_HOST>:<MYSQL_PORT>/stencil
ST_HOST_DATA_DIR=/mnt/<DATA_DRIVE>/Stencil
# Login bootstrap: creates the FIRST admin user at startup while the users
# table is empty. Remove both lines after first boot; manage users in-app later.
ST_ADMIN_EMAIL=admin@example.com
ST_ADMIN_PASSWORD=<CHOOSE_A_STRONG_PASSWORD>
```
`ST_HOST_DATA_DIR` is the Windows folder bind-mounted to `/data` (archives,
completed outputs). Example: `D:\Stencil` → `/mnt/d/Stencil`.

---

## 8. Map invoice directories and supplier profiles

> ### ⚠️ Path template to confirm with infra
> Notes indicate inbound `D:\Astera\Invoices\{customer_id}\{account_number}\pdf`
> and output `...\xls`. Confirm this is current and whether **both**
> `{customer_id}` and `{account_number}` are used.

**8.1 — Mount the invoice tree** into `backend`, `worker`, `watcher` (WSL form):
```yaml
    volumes:
      - ${ST_HOST_DATA_DIR}:/data
      - /mnt/<DATA_DRIVE>/Astera/Invoices:/data/Astera/Invoices
      - ./supplier_profiles:/app/supplier_profiles
```

**8.2 — Set each supplier profile's paths** (in `supplier_profiles/*.json`) to the
matching **container** path:
```json
"directory_paths": {
  "inbound_path": "/data/Astera/Invoices/{customer_id}/{account_number}/pdf",
  "output_path":  "/data/Astera/Invoices/{customer_id}/{account_number}/xls"
}
```
`{account_number}` is substituted from the extracted invoice automatically when
the profile value is null (already implemented), so one profile can serve
multiple accounts.

---

## 9. Build and start

```bash
cd ~/Stencil
docker compose build
docker compose up -d
```

---

## 10. Verify the deployment

```bash
docker compose ps                  # all services running / healthy
docker compose logs -f backend     # confirm "alembic upgrade head" ran + uvicorn started
```
Then:
1. Open `http://<server-ip>:3000` — the web UI should load.
2. Drop a test PDF into a watched inbound folder.
3. Confirm within a few seconds: a row appears in the UI, processing completes,
   and the `.xls` output lands in the corresponding `...\xls` folder.

---

## 11. Auto-start on boot (headless server)

Two layers make the stack survive reboots without an interactive login:

1. **Inside WSL** (already configured): systemd + `systemctl enable docker`, and
   every service has `restart: unless-stopped`, so containers return whenever
   Docker starts.
2. **Start WSL itself at boot** — create a Windows **Task Scheduler** task:
   - **Trigger:** At system startup
   - **Security:** Run whether user is logged on or not; Run with highest privileges
   - **Action → Start a program:**
     ```
     Program:   C:\Windows\System32\wsl.exe
     Arguments: -d Ubuntu -u root -e sh -c "cd /home/<LINUX_USER>/Stencil && docker compose up -d"
     ```

**Validate:** reboot the server, wait a minute, then run `docker compose ps`
(from Ubuntu) and confirm all services are back up.

---

## 12. Day-2 operations

All commands run from `~/Stencil` inside the Ubuntu shell.

| Task | Command |
|------|---------|
| View logs | `docker compose logs -f` (or `-f backend` / `worker` / `watcher`) |
| Stop everything | `docker compose down` |
| Start everything | `docker compose up -d` |
| Restart one service | `docker compose restart worker` |
| Update to new code | `git pull && docker compose build && docker compose up -d` |
| Check status | `docker compose ps` |

**Backups:** the `stencil` MySQL schema (models + audit data) is covered by
Temforce's existing MySQL backups — *confirm with infra*. The `/data` folder
(archived PDFs + completed outputs) lives on the Windows drive and should be
included in the server's file backup.

---

## 13. Configuration reference (key env vars)

| Variable | Purpose | Default |
|----------|---------|---------|
| `ST_OPENAI_API_KEY` | OpenAI key for AI extraction | — (required) |
| `ST_ADMIN_EMAIL` / `ST_ADMIN_PASSWORD` | Bootstrap the first login user (only while users table is empty; remove after first boot) | — (**required once**; without a user the API runs OPEN with a warning) |
| `ST_SESSION_TTL_DAYS` | Login session lifetime | `7` |
| `ST_DATABASE_URL` | SQLAlchemy URL for the existing MySQL | — (required) |
| `ST_REDIS_URL` | Celery broker | `redis://redis:6379/0` |
| `ST_DATA_DIR` | Data dir **inside** the container | `/data` |
| `ST_HOST_DATA_DIR` | Windows folder mounted to `/data` (WSL form) | `/mnt/c/Stencil` |
| `ST_WATCHER_POLL_INTERVAL` | Inbound folder scan interval (seconds) | `1.0` |
| `ST_WATCHER_STABLE_SECONDS` | Wait for a file to stop growing before processing | `3.0` |

> The watcher already uses polling (`PollingObserver`), which is reliable over
> Windows→WSL bind mounts — **no code change required**. Increase
> `ST_WATCHER_POLL_INTERVAL` (e.g. to `2.0`–`5.0`) only if you need to reduce CPU
> when watching many folders.

---

## 14. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `backend` exits on start, can't reach DB | Wrong `<MYSQL_HOST>`, firewall, or user host scope | Verify §4: `bind-address`, firewall port, user `@'%'`, and that `host.docker.internal` resolves (`extra_hosts`) |
| `Access denied for user 'stencil'` | Password mismatch or host not allowed | Re-check the `.env` password and the MySQL `GRANT` host |
| New PDFs not picked up | Wrong mount or profile path | Confirm the `/mnt/<DATA_DRIVE>/...` mount and the profile `inbound_path` match |
| UI loads but no data / API errors | `backend` unhealthy | `docker compose logs backend` |
| Containers don't return after reboot | WSL/Docker didn't auto-start | Re-check §11 (systemd enabled + Task Scheduler entry) |
| AI extraction fails / times out | No outbound access to `api.openai.com` | Confirm outbound HTTPS / proxy with infra |

---

## Appendix — Summary of items to confirm with infra

These are the values/decisions needed to complete the steps above:

1. **MySQL location** (same server vs separate host) → `<MYSQL_HOST>`.
2. **MySQL host / port** → `<MYSQL_HOST>` / `<MYSQL_PORT>`.
3. **MySQL version** (confirm 8.0+).
4. Who **creates the DB + user** (run §4.1 SQL), and the **allowed host scope**.
5. **Firewall / bind-address** allow connections from the Docker/WSL subnet.
6. **Invoice path template** and whether `{customer_id}` + `{account_number}` are both used → `<DATA_DRIVE>` and profile paths.
7. **Outbound access** to `api.openai.com` (direct or proxy).
8. **WSL2 + Docker Engine** install permitted on the server.
