# External / Managed PostgreSQL Setup for TAK Server

This guide covers the pre-flight steps required before using infra-TAK's
**External / Managed Database** deployment mode with a cloud-hosted PostgreSQL
service such as:

- **AWS RDS** (PostgreSQL engine)
- **Azure Database for PostgreSQL** (Flexible Server)
- **Google Cloud SQL** (PostgreSQL)
- **Any self-managed PostgreSQL** reachable over the network

---

## How It Works

```
┌─────────────────────────────────────────┐   JDBC/TCP
│  TAK Server VM  (takserver .deb)        │──────────────►  Cloud PostgreSQL
│  infra-TAK manages certs, config, GD   │               (RDS / Azure / etc.)
└─────────────────────────────────────────┘
```

- infra-TAK installs the **full `takserver` .deb** (not the split core/database packages).
- TAK Server's built-in `SchemaManager` initialises the `cot` database schema on first start.
- infra-TAK patches `CoreConfig.xml` with your endpoint, credentials, and correct JDBC URL.
- Guard Dog monitors the endpoint via TCP and `pg_isready` — no SSH to a database server.

---

## Prerequisites

### 1. PostgreSQL Version

TAK Server requires **PostgreSQL 15** (recommended) or 14.  
Earlier versions are not supported.

> RDS: choose `PostgreSQL 15.x` when creating your instance.  
> Azure: choose `Flexible Server` with PostgreSQL 15.

### 2. Instance Sizing (Starting Point)

| Scale | vCPU | RAM | Storage |
|-------|------|-----|---------|
| < 100 concurrent clients | 2 | 4 GB | 50 GB GP3 |
| 100–500 concurrent clients | 4 | 8 GB | 100 GB GP3 |
| 500–2000 concurrent clients | 8 | 16 GB | 200 GB GP3 |

Enable **automated backups** (7-day retention minimum).  
Enable **Performance Insights** (RDS) or **Query Performance Insight** (Azure) for visibility.

---

## Step 1 — Create the Database and User

Connect to your PostgreSQL instance as the admin/superuser and run:

```sql
-- Create the application database
CREATE DATABASE cot
    ENCODING 'UTF8'
    LC_COLLATE 'en_US.UTF-8'
    LC_CTYPE 'en_US.UTF-8'
    TEMPLATE template0;

-- Create the application user
CREATE USER martiuser WITH PASSWORD 'YOUR_STRONG_PASSWORD_HERE';

-- Grant ownership so TAK Server's SchemaManager can create tables on first start
GRANT ALL PRIVILEGES ON DATABASE cot TO martiuser;

-- Required for schema creation (PostgreSQL 15+)
\c cot
GRANT ALL ON SCHEMA public TO martiuser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO martiuser;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO martiuser;
```

> **Note:** TAK Server creates all tables automatically via SchemaManager on first start.
> You do **not** need to run any TAK schema scripts manually.

---

## Step 2 — Network / Firewall Access

The TAK Server VM must be able to reach your database endpoint on **port 5432** (or your configured port).

### AWS RDS

1. Place the RDS instance in a **VPC** (do not use a public endpoint in production).
2. Add an **inbound rule** to the RDS security group:
   - Type: `PostgreSQL`
   - Port: `5432`
   - Source: TAK Server VM's **private IP** or security group ID
3. If TAK Server is outside AWS, use a **VPN or VPC peering** rather than a public endpoint.

```
# From the TAK Server VM, test reachability:
timeout 5 bash -c '</dev/tcp/YOUR-RDS-ENDPOINT.rds.amazonaws.com/5432' && echo OPEN || echo CLOSED
```

### Azure Database for PostgreSQL

1. Under **Networking → Firewall rules**, add the TAK Server VM's **public or private IP**.
2. If both resources are in the same VNet, enable **Private endpoint** or **VNet integration** for security.
3. Note: Azure Flexible Server requires **SSL by default**. TAK Server connects via standard JDBC without client certs, so disable the `require_secure_transport` parameter if connection fails:
   ```
   Server parameters → require_secure_transport → OFF
   ```
   Alternatively, configure the JDBC URL with `sslmode=require` — contact support if needed.

### Google Cloud SQL

1. Under **Connections**, add the TAK Server VM's public IP to **Authorized networks**.
2. For private connectivity, use **Private IP** with VPC peering to the TAK Server VM's project.

---

## Step 3 — Verify Connectivity from the TAK Server VM

SSH into your TAK Server VM and run:

```bash
# TCP test
timeout 8 bash -c '</dev/tcp/YOUR-DB-ENDPOINT/5432' && echo OPEN || echo CLOSED

# pg_isready (if installed)
pg_isready -h YOUR-DB-ENDPOINT -p 5432 -U martiuser -d cot

# Full auth test (requires postgresql-client)
PGPASSWORD=YOUR_PASSWORD psql -h YOUR-DB-ENDPOINT -p 5432 -U martiuser -d cot -c "SELECT version();"
```

All three should succeed before you proceed in infra-TAK.

---

## Step 4 — Configure infra-TAK

In the infra-TAK web UI, go to **TAK Server → Deploy TAK Server** and:

1. Select **External / Managed DB** as the deployment mode.
2. Fill in:
   - **DB Endpoint** — your RDS/Azure FQDN or IP
   - **Port** — `5432` (default)
   - **Database Name** — `cot`
   - **Username** — `martiuser`
   - **Password** — the password from Step 1
3. Click **1. Save Config**
4. Click **2. Test Connection** — all checks should pass.
5. Upload the full `takserver_X.X_all.deb` package.
6. Fill in the certificate information.
7. Click **Deploy TAK Server**.

infra-TAK will:
- Install the `.deb` package on this VM.
- Patch `/opt/tak/CoreConfig.xml` with your endpoint and credentials.
- Start TAK Server — SchemaManager will create all tables in the `cot` database on first boot.

---

## Step 5 — Guard Dog Monitoring

After deploying Guard Dog, it monitors your external endpoint with:
- **TCP connectivity check** every 5 minutes — alerts after 3 consecutive failures.
- **`pg_isready` check** — if the client is installed locally.
- **No SSH** — Guard Dog cannot restart a managed database; alerts direct you to the cloud console instead.

Alert emails include:
- Failure count and timestamp
- Suggested cloud console actions
- Commands to verify connectivity from the TAK Server VM

---

## PostgreSQL Parameters (Recommended)

These parameters improve TAK Server performance:

```sql
-- Run as superuser on the cot database
ALTER SYSTEM SET max_connections = 200;
ALTER SYSTEM SET shared_buffers = '256MB';       -- 25% of instance RAM
ALTER SYSTEM SET work_mem = '16MB';
ALTER SYSTEM SET maintenance_work_mem = '128MB';
ALTER SYSTEM SET wal_level = 'replica';
ALTER SYSTEM SET checkpoint_completion_target = 0.9;
ALTER SYSTEM SET random_page_cost = 1.1;         -- for SSDs
SELECT pg_reload_conf();
```

> On RDS/Azure you set these as **Parameter Group** / **Server Parameters** values in the console — you cannot use `ALTER SYSTEM` directly.

---

## TAK Server `.deb` Package Reference

| Mode | Package to upload |
|------|-------------------|
| One Server | `takserver_X.X_all.deb` |
| Split Server | `takserver-database_X.X_all.deb` + `takserver-core_X.X_all.deb` |
| **External / Managed DB** | **`takserver_X.X_all.deb`** (same as One Server) |

Download from [tak.gov](https://tak.gov) — you need a free TAK.gov account.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| TCP test shows `CLOSED` | Firewall / security group blocking 5432 | Add inbound rule for TAK Server VM IP |
| `pg_isready` returns `no response` | DB not started or wrong endpoint | Check cloud console for instance status |
| psql auth fails | Wrong password or username | Re-run SQL from Step 1, verify credentials |
| TAK Server starts but can't connect to DB | CoreConfig.xml has wrong JDBC URL | Use infra-TAK "Sync DB Password" or re-deploy |
| Azure SSL error in TAK Server logs | `require_secure_transport = ON` | Set to OFF in Azure server parameters (or contact support) |
| SchemaManager errors on first start | `martiuser` lacks schema privileges | Re-run `GRANT ALL ON SCHEMA public TO martiuser;` |
