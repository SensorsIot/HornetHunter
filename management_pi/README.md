# Management Pi — aggregator

Runs on the **single** management host. Collects `BearingReport` messages from
every Kraken Pi, triangulates them into transmitter fixes, and is where the
operator-facing view will live.

> Status: scaffolding. Triangulation works and is tested; the network intake and
> the map UI are not implemented yet.

## Install on the hardware

```bash
./scripts/bootstrap-pi.sh management
```

See [docs/deployment.md](../docs/deployment.md) for config, systemd and updates.

## CLI

```bash
hornethunter-management --config /etc/hornethunter/management.toml
hornethunter-management --fix-from reports.jsonl
```

`--fix-from` reads newline-delimited `BearingReport` JSON and prints the fix as
`lat,lon`, exiting `1` with a reason if the bearings don't cross or fewer than
two stations reported usable signal. It needs no config and no network, so it's
the quickest way to sanity-check geometry from captured station output:

```bash
$ hornethunter-management --fix-from reports.jsonl
47.380396,8.548334
```

## Config

`config.example.toml` documents every key. `[fusion]` controls how bearings are
admitted into a fix (age, confidence floor, minimum station count);
`[stations].expected` is the list used to notice a station that has gone quiet.

## Layout

```
src/hornethunter_management/cli.py   entry point
systemd/                            unit file
config.example.toml                 copy to /etc/hornethunter/management.toml
tests/
```

Geometry and the message schema come from [`shared/`](../shared) — don't fork
them here.
