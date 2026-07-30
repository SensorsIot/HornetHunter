# Kraken Pi — station agent

Runs on **each** KrakenSDR ground station. Reads direction-of-arrival estimates
from the locally attached KrakenSDR and publishes them to the Management Pi as
`BearingReport` messages.

> Status: scaffolding. The config, message contract and CLI are in place; the
> KrakenSDR acquisition loop and the publisher are not implemented yet.

## Install on the hardware

```bash
./scripts/bootstrap-pi.sh kraken
```

See [docs/deployment.md](../docs/deployment.md) for config, systemd and updates.

## CLI

```bash
hornethunter-kraken --config /etc/hornethunter/kraken.toml
hornethunter-kraken --config /etc/hornethunter/kraken.toml --self-test
```

`--self-test` emits one synthetic `BearingReport` as JSON from the configured
station position and exits — useful for checking config and the wire format
without a radio attached. Its `confidence` is `0.0`, so the Management Pi
discards it rather than folding a fake bearing into a fix.

## Config

`config.example.toml` documents every key. `[station].id` must be unique per Pi,
and `[station].latitude/longitude` must be the real antenna position — a fix is
only as good as the station coordinates behind it.

## Layout

```
src/hornethunter_kraken/cli.py   entry point
systemd/                         unit file
config.example.toml              copy to /etc/hornethunter/kraken.toml
tests/
```

Geometry and the message schema come from [`shared/`](../shared) — don't fork
them here.
