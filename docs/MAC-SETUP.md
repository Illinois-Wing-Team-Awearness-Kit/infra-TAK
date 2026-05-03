# New Mac setup — infra-TAK dev environment

Internal doc — dev branch only. Do not merge to main.

Everything you need to be operational on a fresh MacBook within ~15 minutes.

---

## 1. Transfer SSH keys from old Mac

The `.pem` files for Azure VMs **cannot be re-downloaded** from the portal after creation. Copy them first.

**On the old Mac — copy to a USB drive or AirDrop:**
```bash
# See what you have
ls ~/.ssh/

# The ones that matter for infra-TAK:
#   tak-test-1_key.pem, tak-test-2_key.pem, tak-test-3_key.pem  ← Azure VMs
#   id_ed25519_infratak, id_ed25519_infratak.pub                 ← ssdnodes / general VPS
```

**On the new Mac — restore and fix permissions:**
```bash
# Copy files into ~/.ssh/ then:
chmod 600 ~/.ssh/tak-test-1_key.pem
chmod 600 ~/.ssh/tak-test-2_key.pem
chmod 600 ~/.ssh/tak-test-3_key.pem
chmod 600 ~/.ssh/id_ed25519_infratak
chmod 644 ~/.ssh/id_ed25519_infratak.pub

# Test Azure access
ssh -i ~/.ssh/tak-test-3_key.pem azureuser@20.114.56.117 "echo ok"

# Test ssdnodes / other VPS (uses id_ed25519_infratak)
ssh -i ~/.ssh/id_ed25519_infratak root@<vps-ip> "echo ok"
```

---

## 2. GitHub access

```bash
# Install GitHub CLI (easiest auth method)
brew install gh

# Authenticate — opens browser, follow prompts
gh auth login

# Verify
gh auth status
```

Or use SSH key for GitHub (if you prefer):
```bash
# Check if id_ed25519_infratak is registered on GitHub
# Settings → SSH Keys → should see it listed
# If not, add it: gh ssh-key add ~/.ssh/id_ed25519_infratak.pub --title "new macbook"
```

---

## 3. Clone the repo

```bash
mkdir -p ~/GitHub
cd ~/GitHub

# SSH clone (preferred — no password prompts)
git clone git@github.com:takwerx/infra-TAK.git

# HTTPS clone (if SSH not set up yet)
git clone https://github.com/takwerx/infra-TAK.git

cd infra-TAK
git checkout dev        # always work on dev
git log --oneline -5    # confirm you're current
```

---

## 4. Install Cursor

Download from [cursor.com](https://cursor.com) and install. Open the workspace:

```
File → Open Folder → ~/GitHub/infra-TAK
```

Cursor will pick up `.cursorrules` and workspace rules automatically.

---

## 5. Python (for running app.py locally or testing snippets)

```bash
# Check if Python 3 is already there (usually yes on macOS)
python3 --version

# If not, install via Homebrew
brew install python3

# Quick check — app.py dependencies (Flask, gunicorn, paramiko, etc.)
# These are installed on the server by start.sh, not needed locally for editing
# but useful if you want to run syntax checks:
python3 -c "import ast; ast.parse(open('app.py').read()); print('syntax ok')"
```

---

## 6. Verify everything works

```bash
# Confirm git identity
git config user.name
git config user.email

# Confirm on dev branch, up to date
cd ~/GitHub/infra-TAK
git status
git log --oneline -3

# Test SSH to each active box
ssh -i ~/.ssh/tak-test-3_key.pem azureuser@20.114.56.117 "grep '^VERSION' ~/infra-TAK/app.py"
```

---

## Key files checklist

| What | Location on Mac | Notes |
|------|----------------|-------|
| Azure VM SSH keys | `~/.ssh/tak-test-*.pem` | Cannot be re-downloaded — transfer from old Mac |
| VPS SSH key | `~/.ssh/id_ed25519_infratak` | Transfer from old Mac |
| infra-TAK repo | `~/GitHub/infra-TAK/` | Clone fresh or copy |
| Cursor workspace | Opens from repo folder | No extra setup |
| GitHub auth | `gh auth login` | One-time browser flow |

---

## Active server quick-reference

| Box | IP | SSH command |
|-----|----|-------------|
| tak-test-3 (Azure) | 20.114.56.117 | `ssh -i ~/.ssh/tak-test-3_key.pem azureuser@20.114.56.117` |

For full Azure SSH notes see `docs/AZURE-SSH.md`.
For VPS pull/restart see `docs/PULL-AND-RESTART.md`.
For git release workflow see `docs/COMMANDS.md` → "Merge dev → main".
