# Deploying to the two Pis

One repo, two hardware targets. Each Pi checks out only the code it runs.

```
                 ┌──────────────────────────┐
   KrakenSDR ───►│  Kraken Pi   kraken_pi/  │──┐
                 └──────────────────────────┘  │  BearingReport (JSON)
                 ┌──────────────────────────┐  │
   KrakenSDR ───►│  Kraken Pi   kraken_pi/  │──┤
                 └──────────────────────────┘  │
                                               ▼
                        ┌────────────────────────────────────┐
                        │ Management Pi   management_pi/     │
                        │ collects bearings → triangulates   │
                        └────────────────────────────────────┘

   both sides import shared/ — the geometry and the wire contract
```

## Why sparse-checkout

Both Pis are clients of the same repository and the same `main` branch. Rather
than branching per device — which drifts and turns every update into a merge —
each Pi enables a **git sparse-checkout** and materialises only its own target
directory plus `shared/`, `docs/` and `scripts/`.

That gives what you want on the hardware:

- `git pull` brings down only the files that Pi cares about.
- `git push` works normally; nothing is special about a Pi's commits.
- `git commit -a` on a Pi cannot accidentally touch the other target — those
  files are not in its working tree.
- History stays linear and shared. The Management Pi sees station changes the
  moment it pulls, and `shared/` can never fork between the two.

Sparse-checkout hides files from the working tree; it does not restrict what
you're *allowed* to commit. Editing `shared/` on a Pi is intentionally possible,
because a contract change has to be committed from somewhere.

## First-time setup on a Pi

```bash
sudo apt install -y git python3-venv
git clone --filter=blob:none --no-checkout https://github.com/SensorsIot/HornetHunter.git
cd HornetHunter
./scripts/bootstrap-pi.sh kraken        # or: management
```

`scripts/bootstrap-pi.sh` applies the sparse set, creates `.venv`, and installs
`shared/` followed by the target package. It prints the config and systemd steps
when it finishes. Re-running it is safe.

To bootstrap and clone in one step from anywhere:

```bash
curl -fsSL https://raw.githubusercontent.com/SensorsIot/HornetHunter/main/scripts/bootstrap-pi.sh \
  | bash -s -- kraken /opt/hornethunter
```

## Configure

Config lives outside the repo so a `git pull` can never clobber it, and so the
station's coordinates aren't published:

```bash
sudo install -d /etc/hornethunter
sudo cp kraken_pi/config.example.toml /etc/hornethunter/kraken.toml
sudo nano /etc/hornethunter/kraken.toml
```

Each Kraken Pi needs its own `station.id` and its true antenna position. The
Management Pi lists the station IDs it expects under `[stations].expected`.

## Run as a service

```bash
sudo cp kraken_pi/systemd/hornethunter-kraken.service /etc/systemd/system/
sudo useradd --system --no-create-home hornethunter || true
sudo systemctl daemon-reload
sudo systemctl enable --now hornethunter-kraken
journalctl -u hornethunter-kraken -f
```

The unit expects the venv at `/opt/hornethunter/.venv`. If you cloned somewhere
else, edit `ExecStart` or clone to `/opt/hornethunter`.

## Updating a Pi

```bash
cd /opt/hornethunter
git pull
./.venv/bin/pip install -e shared -e kraken_pi     # only if deps changed
sudo systemctl restart hornethunter-kraken
```

## Checking what a Pi has

```bash
git sparse-checkout list      # the directories in play
git status                    # only ever shows this target's files
```

To widen a checkout temporarily (e.g. to read the other target's code):

```bash
git sparse-checkout set kraken_pi management_pi shared docs scripts
git sparse-checkout set kraken_pi shared docs scripts    # back to normal
```

## Development on the DevVM

The devcontainer is the one place that carries **both** targets — a full
checkout, all three packages installed editable, so `pytest` covers the whole
system:

```bash
ssh -p 2224 dev@dev-1.local
cd /workspaces/HornetHunter
pytest && ruff check . && mypy shared/src kraken_pi/src management_pi/src
```

CI mirrors the split: `ci-kraken.yml` and `ci-management.yml` are filtered by
path, so a station-only change doesn't rebuild the management side. A change
under `shared/` triggers both, which is the point.
