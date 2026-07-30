"""Internal direction-of-arrival measurement (FSD §9.3, §12).

One frozen record unifies the three DoA sources (real KrakenSDR, simulator,
synthetic) so nothing downstream branches on backend (FR-12.3). The feed's `conf`
is **not** normalised to 0..1 — values above 100 occur (§9.3) — so it is carried
as-is and only clamped when quantised into the wire record.

`mono_ts` is a monotonic-clock reading in seconds captured when the measurement was
adapted; the pipeline derives `age_ms` from it (FR-9.3) and never stores an absolute
timestamp (§9.5).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Measurement:
    """One DoA estimate, source-independent."""

    bearing_deg: float
    confidence: float
    power_dbm: float
    freq_hz: float
    latitude: float | None
    longitude: float | None
    adc_overdrive: bool
    squelch_open: bool
    num_corr_sources: int
    snr: float
    mono_ts: float

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None
