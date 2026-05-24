# Edge Bridge module plan (saved for later)

Date: 2026-03-31

## Goal

Add an `infra-TAK` module that deploys a public Oracle Free Tier VPS "anchor" and forwards traffic over NetBird to a traveling edge box (NUC), so the NUC can be plugged in anywhere and still be reachable.

## Module concept

- Name candidates: `Edge Bridge` or `NUC Edge`
- Control plane: `infra-TAK` console -> SSH to Oracle VPS -> deploy/update services
- Data plane: Internet -> Oracle static IP -> bridge/proxy -> NetBird -> NUC service

## What the module manages

1. Anchor host bootstrap
   - Docker install and baseline hardening
   - Firewall baseline
   - NetBird client install/join
   - Reverse proxy or L4 forwarding service setup

2. Edge enrollment pack
   - One-shot NUC join script (install NetBird, enroll, verify)
   - Optional systemd reconnect/health helper

3. Service publish map
   - Public port -> edge NetBird IP:target port
   - Enable/disable published services

4. Day-2 operations
   - Redeploy anchor
   - Rotate/replace setup keys
   - Health checks and heartbeat visibility
   - Simple rollback to previous known-good config

## MVP workflow

1. Save Oracle SSH target in module settings
2. Click `Deploy Anchor`
3. Generate `Edge Join Script`
4. Run join script on NUC once
5. Verify green health state and published endpoints

## Why this fits infra-TAK

- Reuses existing remote deployment model (SSH target, remote probes, remote status checks)
- Starts with known-good patterns already used in the project
- Keeps first release small and repeatable before adding advanced routing options
