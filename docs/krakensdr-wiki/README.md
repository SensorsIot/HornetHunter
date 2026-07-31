# Vendored KrakenSDR documentation

A **snapshot** of selected pages from the upstream KrakenSDR wiki, vendored so the
FSD can cite stable text and so the docs are available in the field without
internet.

- **Source:** <https://github.com/krakenrf/krakensdr_docs/wiki>
- **Snapshot:** commit `90badb7`, 2026-06-14
- **Retrieved:** 2026-07-30

Upstream is authoritative. Re-sync with:

```bash
git clone --depth 1 https://github.com/krakenrf/krakensdr_docs.wiki.git
```

## What is here, and why

| Page | Why it matters to HornetHunter |
|------|-------------------------------|
| `04.-Antenna-Array-Setup.md` | Array geometry rules. Fixes the physical build and the `ant_spacing_meters` / `ant_arrangement` settings, and bounds achievable bearing accuracy. |
| `05.-KrakenSDR-Web-Interface-Controls.md` | Semantics and constraints of every parameter the management UI distributes. The reference for our parameter form. |
| `03.-Direction-Finding-Background-Theory.md` | DoA fundamentals; underpins the error model used when triangulating. |
| `02.-Direction-Finding-Quickstart-Guide.md` | Station bring-up sequence, for the deployment procedure. |
| `07.-KrakenSDR-Troubleshooting.md` | Field diagnostics for a station that reports no or bad bearings. |
| `13.-Appendix.md` | Reference tables. |
| `Home.md` | Upstream index, for navigation. |

**Deliberately not copied:** `01` (hardware shopping list), `06` (Android/iOS app),
`08` (GNU Radio block), `09` (VirtualBox/Docker), `10` (KerberosSDR), `11` (Kraken
Pro *Cloud* Mapper — we use `Kraken Pro Local` and send nothing to the cloud),
`12` (TAK server). None apply to this system.

## Constraints extracted from these pages

Load-bearing facts, pulled out so they are not buried. See
[../hornethunter-fsd.md](../hornethunter-fsd.md) §12–§13 and §16 for the API contract.

**Array geometry** (`04`)

- Interelement spacing `I_e = s·λ`, with the spacing multiplier `s` **strictly
  below 0.5** or the array is ambiguous — more than one valid bearing solution.
  Usable range 0.2–0.5; upstream typically uses **0.33**. Below 0.2 resolution
  becomes unacceptable for 5 elements.
- UCA radius for `n` elements: `r = s·λ / √(2(1−cos(360/n)))`
- At **434 MHz** (λ = 0.691 m): `s = 0.33` → `I_e ≈ 0.228 m` → 5-element UCA
  radius **≈ 0.19 m**. The stock template's 200 mm radius hole covers
  255–637 MHz, so it fits.
- ⚠️ `settings.json` ships `ant_spacing_meters = 0.15`, which at 434 MHz is
  `s ≈ 0.22` — legal but near the low-resolution floor. Our deployment should set
  this deliberately, not inherit it.

**Achievable accuracy** (`04`) — best case, ignoring multipath:

| Array | 5 elements at 0.5λ |
|-------|--------------------|
| UCA (360° coverage) | **≈ 8°** |
| ULA (180°, higher accuracy) | **≈ 3.4°** |

This is the dominant term in any triangulation error budget — an 8° bearing error
at 1 km is a ±140 m cross-range uncertainty.

**Build tolerance** (`04`)

- Identical antennas and **identical coax lengths**, matched to within ~1 cm up to
  about 900 MHz. At 400 MHz a 1 cm mismatch is **≈ 7°** of phase distortion; at
  800 MHz, ≈ 14°. The KrakenSDR does not compensate for this.

**Squelch and burst signals** (`05`)

- Squelch is what stops the DSP emitting **random bearings from noise** — without
  it every station reports garbage whenever the tag is silent. Max value 0 dB.
- **Squelch requires `spectrum_calculation = "Single"`** ("you must use this mode
  if you are using the squelch feature"). A real coupling between two settings our
  parameter form must enforce.
- **`en_optimize_short_bursts`** helps detect narrowband CW bursts with pulse
  periods under ~50 ms — likely relevant if the hornet tag is a pulsed CW beacon.

**VFOs** (`05`)

- A VFO frequency must lie inside the **2.4 MHz** DAQ bandwidth around
  `center_freq`.
- Up to 16 VFOs, but on a Pi 4 more than 3–4 continuously-active VFOs can slow
  processing enough to miss intermittent signals. Squelched channels cost nothing.
- `output_vfo = ALL` only works with the Kraken App / Kraken Pro formats — which
  includes the `Kraken Pro Local` mode we use.
