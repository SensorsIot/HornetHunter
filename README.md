# 🐝 HornetHunter

Radio direction finding for tracking transmitter-tagged invasive hornets. Two
KrakenSDR ground stations each measure a bearing to a tagged hornet; a management
host polls them over LoRa (or WLAN when co-located), shows the bearings live, and
pushes KrakenSDR configuration out to every station.

**v1 shows bearings only.** Triangulating them into a position fix is v2 — the
geometry already exists and is tested, but is unused in v1. See
[docs/hornethunter-fsd.md](docs/hornethunter-fsd.md).

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-C51A4A?style=flat&logo=raspberrypi&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green.svg)

## What it does

- **One link protocol over two carriers.** A self-delimiting HH-Link frame format
  runs identically over the LoRa DTUs and over WLAN, with **stop-and-wait ARQ** so
  bearings and settings get through despite packet loss, and **automatic
  per-station WLAN↔LoRa failover**.
- **Live numeric dashboard.** A local web UI on the management host pushes per-station
  values over a WebSocket: bearing, confidence, power, measurement age, active
  carrier, retry-based link health, and configuration state. Numbers only — no
  cloud, no external assets.
- **Config distribution with integrity.** KrakenSDR settings are sent as **deltas**
  and continuously verified by a **canonical CRC** computed from each station's
  read-back, so a station that drifts from its intended configuration is flagged
  within one poll cycle.
- **RF stays with the operator.** Frequency, array geometry, power, and regulatory
  compliance are set manually by the specialist; the software only transports the
  data securely and applies whatever it is told.

## Architecture

Three Python packages, layered so the code mirrors the design — a strict one-way
dependency from foundation up to application logic:

```
 L2  application logic   poll scheduler · transport selector · health ·
                         parameter distribution · bearing pipeline
 L1  interfaces          HH-Link frame + ARQ · DTU AT provisioning ·
                         Kraken DoA source · Kraken settings · web UI
 L0  foundation          LoRa DTU & byte carriers · krakensdr_doa · Raspberry Pi
```

| Target | Hardware | Runs | Docs |
|--------|----------|------|------|
| 📡 **Kraken Pi** | one per station, KrakenSDR attached | answers polls, reads the DoA feed, sends bearings, applies settings | [kraken_pi/README.md](kraken_pi/README.md) |
| 🗺 **Management Pi** | one per network | polls stations, serves the web UI, distributes settings | [management_pi/README.md](management_pi/README.md) |
| 🔗 **shared** | installed on both | the wire contract: framing, ARQ, field registry, geometry | [shared/README.md](shared/README.md) |

```
Kraken Pi ──┐   POLL ─────────────►                    ┌─ numeric web UI
Kraken Pi ──┼── ◄──── BEARING ──── Management Pi ──────┤
Kraken Pi ──┘   ◄─ ACK ─ config delta ─►               └─ settings distribution
                     (LoRa / WLAN, ARQ)
```

The management host is the master and owns the schedule; stations only ever answer
a poll. Full design contract: **[docs/hornethunter-fsd.md](docs/hornethunter-fsd.md)**.

## Setting up a Pi

```bash
git clone --filter=blob:none --no-checkout https://github.com/SensorsIot/HornetHunter.git
cd HornetHunter
./scripts/bootstrap-pi.sh kraken          # or: management
```

The bootstrap script puts the clone into a git **sparse-checkout** so the Pi only
ever materialises its own target, `shared/`, `docs/` and `scripts/`, then builds a
venv and installs the packages (with their dependencies — `websocket-client` on a
station, `flask`/`flask-sock` on the management host). Pull and push work normally
against the same `main` — no per-device branches. Full walk-through, including
systemd, the WLAN access point, and updates:
**[docs/deployment.md](docs/deployment.md)**.

Once the management service is running, open the dashboard at
`http://<management-pi>:8000` (host and port come from `[server]` in its config).

## Development

Development happens on the DevVM devcontainer, the one checkout that carries both
targets:

```bash
ssh -p 2224 dev@dev-1.local
cd /workspaces/HornetHunter
pytest                                                        # all targets
ruff check .
mypy shared/src kraken_pi/src management_pi/src
```

Outside the container, install the three packages editable — `shared` first, since
the targets depend on it by name:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e shared -e kraken_pi -e management_pi -r requirements-dev.txt
```

CI is split like the repo: [`ci-kraken.yml`](.github/workflows/ci-kraken.yml) and
[`ci-management.yml`](.github/workflows/ci-management.yml) are path-filtered, so a
station-only change doesn't rebuild the management side; a change under `shared/`
runs both.

## Documentation

- 📋 [Functional Specification (FSD)](docs/hornethunter-fsd.md) — the design contract
- 🚀 [Deployment & the two-Pi split](docs/deployment.md)
- 📡 [SX1262 LoRa DTU notes](docs/lora-dtu-sx1262.md)
- 🛰 [KrakenSDR integration](docs/krakensdr-integration.md)

## 🔗 Related

The station firmware and the KrakenSDR simulator live in
[SensorsIot/KrakenSimulator](https://github.com/SensorsIot/KrakenSimulator).

## 📄 License

MIT — see [LICENSE](LICENSE).
