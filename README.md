# HornetHunter

Radio direction finding (RDF) tools for tracking invasive hornets.

Host-side Python tooling: it processes direction-of-arrival data from ground
stations to triangulate a transmitter's position. The firmware and simulator
that the ground stations themselves run live in a separate repository,
[SensorsIot/KrakenSimulator](https://github.com/SensorsIot/KrakenSimulator).

> Early scaffolding — the package currently exposes only a CLI stub.

## Development

The project ships a Python 3.11 devcontainer (`.devcontainer/`). Open the folder
in VS Code and reopen in the container, or connect over SSH on host port `2224`:

```bash
ssh -p 2224 dev@dev-1.local
```

Install in editable mode with the dev tooling, then run the checks:

```bash
pip install -e . -r requirements-dev.txt
pytest          # tests
ruff check .    # lint
mypy src        # types
```

## Layout

```
src/hornethunter/   package (cli.py — entry point `hornethunter`)
tests/              pytest suite
```

## License

MIT
