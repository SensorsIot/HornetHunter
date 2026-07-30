# shared — code both targets run

Installed on every Pi, imported by both targets. Two things live here, and they
are here precisely because a fork between the station and the management side
would be a silent bug:

| Module | Contract |
|--------|----------|
| `geo.py` | Bearing intersection and triangulation, on a local east/north tangent plane |
| `messages.py` | `BearingReport` — the JSON payload stations send to management |
| `config.py` | TOML loading, with errors that name the missing key |

## Changing the wire format

`messages.py` is the contract between hardware that is updated at different
times. A field addition that old stations simply omit is fine; anything that
changes the meaning of an existing field needs `SCHEMA_VERSION` bumped, at which
point `from_json` rejects mismatched payloads instead of misreading them. Both
Pis must then be updated together.

## Coordinates

`geo.py` projects to a local tangent plane about a reference point. Sub-metre
accurate across the few kilometres an RDF net spans, and wrong for
continent-scale baselines — see the module docstring.

A change here triggers both target CI workflows.
