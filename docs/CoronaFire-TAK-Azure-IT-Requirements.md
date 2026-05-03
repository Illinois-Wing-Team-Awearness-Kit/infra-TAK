# Corona Fire Department
## TAK Situational Awareness Platform
### Azure Infrastructure Requirements — City IT Provisioning Request

---

## 1. Overview

The Corona Fire Department is deploying a production TAK (Team Awareness Kit) situational awareness platform on Microsoft Azure. This system provides real-time geospatial common operating picture (COP) for fire department personnel during incident operations, including unit tracking, drone video integration, GIS data feeds (wildfire perimeters, FAA airspace restrictions), and multi-agency coordination.

This document defines the Azure infrastructure requirements for city IT to provision. The TAK platform must be isolated from all other city infrastructure in a dedicated resource group and virtual network.

---

## 2. Architecture Summary

The deployment consists of two virtual machines in a dedicated Azure Virtual Network. The App Node runs all application services. The DB Node runs only the TAK Server database and has no public internet access.

| Node | Services | VM SKU | vCPU / RAM | Purpose |
|------|----------|--------|------------|---------|
| App Node | TAK Server, CloudTAK, Authentik, TAK Portal, Node-RED, MediaMTX, Postfix | Standard_D16ds_v5 | 16 vCPU / 64 GB | All application services and user-facing interfaces |
| DB Node | PostgreSQL (TAK Server database) | Standard_E4ds_v5 | 4 vCPU / 32 GB | Isolated database tier — no public IP |

---

## 3. Virtual Machine Specifications

### 3.1 App Node — Standard_D16ds_v5

| Parameter | Value |
|-----------|-------|
| VM SKU | Standard_D16ds_v5 |
| vCPU | 16 |
| RAM | 64 GB |
| OS Disk | Premium SSD, 128 GB minimum |
| Data Disk | Premium SSD **P30 (1 TB)** for application and container volumes |
| Operating System | Ubuntu Server 22.04 LTS |
| Public IP | Yes — static public IP required |
| Accelerated Networking | Enabled |
| Availability Zone | Same zone as DB Node |

> **Note on data disk:** The TAK platform's health monitoring system benchmarks disk write throughput and requires a minimum of 200 MB/s. Azure Premium SSD P30 (1 TB) provides 200 MB/s. A P20 (512 GB, 150 MB/s) is insufficient and will trigger continuous disk performance alerts.

### 3.2 DB Node — Standard_E4ds_v5

| Parameter | Value |
|-----------|-------|
| VM SKU | Standard_E4ds_v5 |
| vCPU | 4 |
| RAM | 32 GB |
| OS Disk | Premium SSD, 64 GB minimum |
| Data Disk | Premium SSD P20 (512 GB) for PostgreSQL data directory |
| Operating System | Ubuntu Server 22.04 LTS |
| Public IP | None — private network only |
| Private IP | **Static** — must be assigned as a static private IP (see Section 4.4) |
| Accelerated Networking | Enabled |
| Availability Zone | Same zone as App Node |

---

## 4. Networking Requirements

### 4.1 Virtual Network

- Dedicated Virtual Network (VNet) — isolated from all other city Azure resources
- Address space: `10.10.0.0/16` (or equivalent non-conflicting range)
- Two subnets: `app-subnet` (`10.10.1.0/24`) and `db-subnet` (`10.10.2.0/24`)
- Both VMs in the same **Availability Zone** for minimum inter-node latency
- Both VMs in a **Proximity Placement Group** to ensure physical co-location within the zone
- No VNet peering to city infrastructure — this platform must remain isolated

### 4.2 Network Security Group — App Node

The following inbound rules must be configured on the App Node NSG:

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 22 | TCP | City IT admin IPs only | SSH administration |
| 80 | TCP | Any | HTTP — redirects to HTTPS via Caddy |
| 443 | TCP | Any | HTTPS — CloudTAK, TAK Portal, Node-RED, Authentik, MediaMTX editor (all via Caddy reverse proxy) |
| 5001 | TCP | Fire dept admin IPs | TAK platform management console (HTTPS) |
| 8089 | TCP | Any | TAK Server — ATAK / WinTAK / iTAK client connections (TLS) |
| 8443 | TCP | Any | TAK Server — admin interface, client certificate auth (HTTPS) |
| 8446 | TCP | Any | TAK Server — admin interface, LDAP/password auth (HTTPS) |
| 8554 | TCP | Any | MediaMTX — RTSP drone video stream |
| 8889 | TCP | Any | MediaMTX — WebRTC / HLS video playback |
| 8189 | UDP | Any | MediaMTX — WebRTC signaling |
| 9000–9100 | UDP | Any | MediaMTX — WebRTC RTP media ports |

> **Note:** Port 5001 is the fire department's primary management interface for the entire TAK platform. It should be accessible to fire department technical staff and restricted to known admin IPs where possible.

> **Note:** Postfix is configured for outbound email relay only and binds exclusively to localhost. No inbound rule for port 25 is required or should be added — doing so creates an unnecessary security exposure.

### 4.3 Network Security Group — DB Node

The DB Node has no public IP and must only accept traffic from the App Node and city IT admin systems.

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 5432 | TCP | App Node private IP | PostgreSQL — TAK Server database connection |
| 8080 | TCP | App Node private IP | TAK platform health monitor agent — remote database monitoring and alerting |
| 22 | TCP | App Node private IP + city IT admin IPs | SSH — platform management (App Node requires SSH access to configure and maintain the DB Node) |
| All others | — | DENY | All other inbound traffic denied |

> **Critical:** The App Node must be able to SSH into the DB Node. The platform management software runs on the App Node and remotely configures the database server, manages backups, and performs database maintenance. Restricting SSH to city IT admin IPs only will prevent the platform from functioning.

### 4.4 Static Private IP — DB Node

The DB Node's private IP address must be configured as **static** (not dynamic) in Azure. The TAK Server application hardcodes the database server's IP address into its configuration files at deployment time. If Azure reassigns the private IP after a reboot or reallocation event, TAK Server will be unable to connect to the database until manually reconfigured.

**How to set:** In the Azure Portal, navigate to the DB Node NIC → IP configurations → set allocation to **Static** before the VM is first started.

---

## 5. Resource Group and Isolation

- Dedicated resource group: `rg-coronafire-tak` (or equivalent naming convention)
- No shared resources with other city departments or Azure subscriptions
- Fire department IT contact must have **Contributor** role on this resource group
- City IT retains **Owner** role for billing and policy compliance

---

## 6. DNS Requirements

- Static public IP assigned to App Node
- DNS A record pointing to App Node public IP — to be coordinated with fire department
- Wildcard subdomain support preferred (e.g., `*.tak.coronafire.gov`) or individual A records for each service

---

## 7. Backup Policy

- Azure Backup enabled on both VMs
- Daily snapshot retention: 30 days minimum
- Backup vault in same resource group
- Recovery Services Vault: standard LRS redundancy is acceptable

---

## 8. Operating System and Software

- Ubuntu Server 22.04 LTS on both nodes
- Docker Engine and Docker Compose plugin — fire department will install and manage
- No additional software or agents required from city IT beyond OS provisioning
- Automatic OS security updates enabled (`unattended-upgrades`)

---

## 9. Access Requirements

- Fire department technical contact requires SSH key-based access to **both** VMs
- Fire department technical contact requires Azure Portal access (Contributor role) on the resource group
- Root/sudo access on both VMs for fire department technical contact
- City IT SSH access for break-glass / emergency administration

---

## 10. Monitoring and Alerting

- Azure Monitor enabled on both VMs
- CPU, memory, and disk alerts at 85% threshold — notify fire department technical contact
- VM availability alert — notify fire department technical contact on any unplanned downtime

---

## 11. Provisioning Checklist for City IT

Before handing off to the fire department technical contact, please confirm:

- [ ] Resource group created with correct RBAC roles
- [ ] VNet and subnets provisioned with no peering to city infrastructure
- [ ] Proximity Placement Group created and both VMs assigned to it
- [ ] Both VMs in the same Availability Zone
- [ ] App Node provisioned: D16ds_v5, Ubuntu 22.04 LTS, static public IP, P30 data disk attached
- [ ] DB Node provisioned: E4ds_v5, Ubuntu 22.04 LTS, **no public IP**, P20 data disk attached
- [ ] **DB Node private IP set to static** before first boot
- [ ] App Node NSG configured with all rules in Section 4.2
- [ ] DB Node NSG configured with all rules in Section 4.3 (including App Node SSH and port 8080)
- [ ] DNS A record created pointing to App Node public IP
- [ ] Azure Backup vault configured on both VMs
- [ ] Azure Monitor alerts configured
- [ ] Fire department technical contact has SSH key access to both VMs
- [ ] Fire department technical contact has Contributor role on resource group

---

## 12. Fire Department Technical Contact

All provisioning questions and access requests should be coordinated with the fire department technical contact. Once VMs are provisioned with OS and SSH access, the fire department will handle all application installation and configuration independently.

---

*Corona Fire Department — TAK Platform Infrastructure Request*  
*Revised April 2026*
