"""TOML configuration loading, shared by both targets."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


def load_config(path: str | Path) -> dict[str, Any]:
    """Read a TOML config file.

    Raises FileNotFoundError with the expected location, so a Pi that was never
    configured fails with an actionable message instead of a KeyError later.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise FileNotFoundError(
            f"no config at {config_path} — copy config.example.toml and edit it"
        )
    with config_path.open("rb") as handle:
        return tomllib.load(handle)


def require(config: dict[str, Any], section: str, key: str) -> Any:
    """Fetch config[section][key], naming what is missing if it is absent."""
    try:
        return config[section][key]
    except KeyError as exc:
        raise KeyError(f"missing required config value [{section}].{key}") from exc
