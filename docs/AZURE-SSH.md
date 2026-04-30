# Azure VM — SSH access reference

Internal doc — dev branch only. Do not merge to main.

---

## SSH key locations (local Mac)

| VM | Key file |
|----|---------|
| tak-test-1 | `~/.ssh/tak-test-1_key.pem` |
| tak-test-2 | `~/.ssh/tak-test-2_key.pem` |
| tak-test-3 | `~/.ssh/tak-test-3_key.pem` |

Standard infra-TAK boxes (ssdnodes etc.) use `~/.ssh/id_ed25519_infratak`.

---

## Connect

```bash
# tak-test-3 (current Azure test box — East US, D8as_v5)
ssh -i ~/.ssh/tak-test-3_key.pem azureuser@20.114.56.117

# Generic pattern
ssh -i ~/.ssh/<vm>_key.pem azureuser@<public-ip>
```

**Username is always `azureuser`** on Azure VMs provisioned via the portal with SSH key auth.

**Find the public IP:** Azure Portal → Virtual Machines → select VM → Overview → Public IP address. Or from `start.sh` output (public IP is printed when it differs from private IP, which it always does on Azure).

---

## First time on a new Azure VM

Azure `.pem` keys come from the portal at VM creation time. If you missed the download, the key cannot be retrieved — you must reset SSH auth in the portal (VM → Reset password).

```bash
# Fix permissions if ssh refuses the key
chmod 600 ~/.ssh/tak-test-3_key.pem

# Test connection
ssh -i ~/.ssh/tak-test-3_key.pem azureuser@<ip> "echo ok"
```

---

## Become root (required for infra-TAK commands)

Azure VMs log in as `azureuser` (non-root). Most infra-TAK operations need root:

```bash
sudo su -
# now you are root at /root
```

Or prefix individual commands:
```bash
sudo systemctl restart takwerx-console
sudo ./start.sh
```

---

## Common commands once connected

```bash
# Pull latest and restart console
cd ~/infra-TAK
git fetch origin dev && git checkout -B dev origin/dev
sudo systemctl restart takwerx-console

# Watch console logs live
sudo journalctl -u takwerx-console -f

# Check running containers
docker ps

# Check infra-TAK version
grep '^VERSION' ~/infra-TAK/app.py
```

---

## Azure NSG — required inbound rules

If the console or TAK ports are unreachable, check the NSG attached to the VM's NIC.
See `docs/azure-nsg-infra-tak.json` for the ARM template that adds all required rules at once.

| Port | Protocol | Purpose |
|------|----------|---------|
| 22 | TCP | SSH |
| 443 | TCP | Caddy / HTTPS (Let's Encrypt) |
| 5001 | TCP | infra-TAK console (restrict to your IP) |
| 8089 | TCP | TAK Server CoT TLS |
| 8443 | TCP | TAK Portal HTTPS |
| 8446 | TCP | TAK Server cert enrollment |

---

## VM specs reference (tak-test-3)

| Field | Value |
|-------|-------|
| Size | Standard_D8as_v5 |
| OS | Ubuntu Server 22.04 LTS |
| Region | East US |
| OS disk | P10 64 GiB (managed, ~145 MB/s sync write) |
| vCPUs | 8 (from AMD EPYC 7763) |
| RAM | 32 GB |
| Public IP | 20.114.56.117 |
| Private IP | 10.x.x.x (Azure internal) |
