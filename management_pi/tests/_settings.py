"""Shared helper: a full, valid KrakenSDR settings dict from the registry."""

from __future__ import annotations

from typing import Any

from hornethunter_shared.registry import FIELD_REGISTRY


def full_settings(**overrides: Any) -> dict[str, Any]:
    """Every registry key with a type-appropriate placeholder value."""
    settings: dict[str, Any] = {}
    for spec in FIELD_REGISTRY:
        if spec.type == "enum":
            settings[spec.key] = spec.enum_values[0]
        elif spec.type == "bool":
            settings[spec.key] = False
        elif spec.type == "str":
            settings[spec.key] = "x"
        elif spec.type == "fixed":
            settings[spec.key] = 1.0
        else:
            settings[spec.key] = 1
    settings.update(overrides)
    return settings
