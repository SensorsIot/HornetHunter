# 🐝 HornetHunter

Radio direction finding for tracking invasive hornets. Several KrakenSDR ground
stations each measure a bearing to a transmitter-tagged hornet and report it to a
management host over LoRa (or WLAN when co-located).

**v1 displays bearings only.** Triangulating them into a position fix is v2 — see
[docs/hornethunter-fsd.md](docs/hornethunter-fsd.md).

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-C51A4A?style=flat&logo=raspberrypi&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green.svg)

## 🛠 Hardware targets

One repo, one directory per target. Each Pi checks out only its own code.

| Target | Hardware | Runs | Documentation |
|--------|----------|------|---------------|
| 📡 **Kraken Pi** | one per ground station, KrakenSDR attached | measures bearings, answers polls | [kraken_pi/README.md](kraken_pi/README.md) |
| 🗺 **Management Pi** | one per network | polls stations, displays bearings, distributes settings | [management_pi/README.md](management_pi/README.md) |
| 🔗 **shared** | installed on both | wire contract + geometry | [shared/README.md](shared/README.md) |

```
Kraken Pi ──┐
Kraken Pi ──┼──► BearingReport ──► Management Pi ──► numeric display
Kraken Pi ──┘        (LoRa / WLAN)         └──► settings deltas ──►
```

## 🚀 Setting up a Pi

```bash
git clone --filter=blob:none --no-checkout https://github.com/SensorsIot/HornetHunter.git
cd HornetHunter
./scripts/bootstrap-pi.sh kraken          # or: management
```

The bootstrap script puts the clone into a git **sparse-checkout** so the Pi only
ever materialises its own target, `shared/`, `docs/` and `scripts/`. Pull and
push work normally against the same `main` — no per-device branches, and a Pi
cannot accidentally commit changes to the other target. Full walk-through,
including systemd and updates: **[docs/deployment.md](docs/deployment.md)**.

## 💻 Development

Development happens on the DevVM devcontainer, which is the one checkout that
carries both targets:

```bash
ssh -p 2224 dev@dev-1.local
cd /workspaces/HornetHunter
pytest                                                        # all targets
ruff check .
mypy shared/src kraken_pi/src management_pi/src
```

Outside the container, install the three packages editable — `shared` first,
since the targets depend on it by name:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e shared -e kraken_pi -e management_pi -r requirements-dev.txt
```

CI is split the same way as the repo: [`ci-kraken.yml`](.github/workflows/ci-kraken.yml)
and [`ci-management.yml`](.github/workflows/ci-management.yml) are filtered by
path, so a station-only change doesn't rebuild the management side. A change
under `shared/` runs both.

## 📚 Documentation

- 📋 [Functional Specification (FSD)](docs/hornethunter-fsd.md) — the design contract
- 🚀 [Deployment & the two-Pi split](docs/deployment.md)
- 📡 [SX1262 LoRa DTU notes](docs/lora-dtu-sx1262.md)
- 🛰 [KrakenSDR integration](docs/krakensdr-integration.md)

## 🔗 Related

The station firmware and the KrakenSDR simulator live in
[SensorsIot/KrakenSimulator](https://github.com/SensorsIot/KrakenSimulator).

## 📄 License

MIT — see [LICENSE](LICENSE).
