# v0.7.2-alpha — Tablet Command AVL Integration

## What's New

### Tablet Command AVL → ATAK CoT Streaming

Agencies using [Tablet Command](https://tabletcommand.com) can now stream vehicle positions (fire engines, ambulances, command vehicles, helicopters) directly into ATAK as live CoT events.

**How it works:**
- In the Configurator, click **🚒 Tablet Command AVL** → fill in your agency name and Feature Service URL
- Click **Deploy & Activate** — a dedicated Node-RED tab is created for that agency
- The tab polls the Tablet Command FeatureServer every 1–5 minutes (configurable)
- Each vehicle becomes a live CoT point on the TAK map, updating in place

**CoT type auto-detection** based on radio name prefix:
| Prefix | CoT Type |
|--------|---------|
| `E`, `ENG` | Engine (a-f-G-E-V-C) |
| `T`, `TRK`, `LAD` | Truck/Ladder (a-f-G-E-V-C) |
| `M`, `MED`, `AMB`, `ALS`, `BLS` | Medic (a-f-G-E-V-M) |
| `BC`, `BAT`, `CHIEF`, `AC`, `DC` | Chief/Command (a-f-G-E-V-C) |
| `H`, `HELO`, `AIR`, `HT` | Helicopter (a-f-A-C-H) |
| `WT`, `WAT`, `WATER` | Water Tender (a-f-G-E-V-C) |
| `RES`, `RESCUE`, `SQ`, `SQUAD` | Rescue/Squad (a-f-G-E-V-C) |

### Known Units / Remapping Table (COTProxy replacement)
Each agency config has a per-agency remapping table where you can:
- Override radio names with custom callsigns (e.g. `CA342` → `Corona Engine 42`)
- Override CoT types for specific units
- Upload/download the table as a CSV (`radioName,callsign,cotType`)

### Multi-Agency Support
Each agency gets its own named config card in the Saved Configurations list, its own Node-RED tab, and its own remapping table. Add as many agencies as needed.

## Architecture

- **No KML, no DataSync** — pure CoT stream via TCP :8089 to the TAK server
- Configs stored in Node-RED global context under `tc_configs`
- Known units stored under `tc_units_<configId>`
- New API endpoints: `POST /tc/config/save`, `POST /tc/config/delete`, `GET /tc/config/load`, `POST /tc/units/save`, `GET /tc/units/load`
- TC engine tab template (`TC_ENGINE_TAB_TEMPLATE`) injected into `configurator.html` by `build-flows.js`

## Files Changed
- `nodered/build-flows.js` — `makeTCEngineTab()`, TC config persistence nodes, TC template injection
- `nodered/configurator.html` — TC source button, TC panel, TC JavaScript helpers
- `app.py` — VERSION bumped to 0.7.2-alpha
- `README.md` — latest release updated
- `docs/RELEASE-v0.7.2-alpha.md` — this file

## Deploying

```bash
git fetch origin --tags
git checkout -B dev origin/dev
./nodered/deploy.sh --no-pull
```

Then go to the Configurator → click **🚒 Tablet Command AVL** → enter agency name + Feature Service URL → **Deploy & Activate**.
