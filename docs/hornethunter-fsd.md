# HornetHunter — Functional Specification Document (FSD)

**Version:** 1.0 (v1 scope) · **Status:** draft · **Date:** 2026-07-30

---

## 1. System Overview

### 1.1 Purpose

HornetHunter is a radio-direction-finding network for tracking transmitter-tagged
invasive hornets. Two ground stations, each a Raspberry Pi with an attached
KrakenSDR, measure a direction of arrival (DoA) to a tagged hornet. A Management
Pi collects those bearings, displays them live, and distributes KrakenSDR
configuration to the stations.

v1 provides two capabilities and makes their behaviour measurable:

1. the **data link** between the Management Pi and the stations, including its
   reliability mechanism and its health reporting;
2. the **interface to the KrakenSDR software** — reading bearings out of it and
   pushing configuration into it.

### 1.2 Scope

**In scope**

- The station↔management data link: framing, reliability (ARQ), addressing, poll
  scheduling, and link health indication.
- Automatic selection between the WLAN and LoRa carriers.
- Reading DoA measurements from the KrakenSDR software.
- Distributing the KrakenSDR configuration set to stations, and detecting when a
  station's configuration diverges from what the Management Pi holds for it.
- A numeric management interface and a structured debugging log.

**Out of scope — operator-owned**

- **All RF engineering.** Frequency selection and planning, antenna array geometry
  and spacing, array orientation and heading calibration, transmit power, link
  budget, range, band selection, and regulatory compliance are decided and
  performed manually by the operator.
- Radio parameters are **values the software applies**, not values the software
  chooses or validates for physical correctness. The system shall apply whatever
  the operator configures.
- Triangulation of bearings into a position fix — v2 (§3.3).

### 1.3 Users

| Role | Interaction |
|------|-------------|
| Operator | Runs the hunt. Uses the management UI to set KrakenSDR parameters and watch bearings and link health. |
| Developer | Uses the debugging log and the bench harness to characterise the link and the Kraken interface. |

In v1 these are the same person.

### 1.4 Goals and non-goals

**Goals**

- G1 — Deliver bearings from both stations to the Management Pi continuously, with
  loss made visible.
- G2 — Distribute configuration to stations, and make configuration divergence
  detectable.
- G3 — Make the link's condition legible at a glance and auditable after the fact.
- G4 — Run identically over WLAN and LoRa.

**Non-goals**

- N1 — No position fix in v1.
- N2 — No graphical display: numbers only.
- N3 — No cloud service, no internet dependency of any kind at runtime.
- N4 — No automatic remediation of persistent faults; faults are indicated and a
  human resolves them.

### 1.5 High-level flow

```
   KrakenSDR ──► krakensdr_doa ──WebSocket──► Station Agent ──┐
   (station 1)   (+ middleware)                               │
                                                     HH-Link  │  bearings
   KrakenSDR ──► krakensdr_doa ──WebSocket──► Station Agent ──┤  (LoRa or WLAN)
   (station 2)   (+ middleware)                               │
                                                              ▼
                                              ┌──────────────────────────┐
                                              │  Management Pi           │
                                              │  poll · health · UI      │
                                              │  numeric display + log   │
                                              └──────────────────────────┘
                        configuration deltas ──────────┘ (same link, reverse)
```

---

## 2. System Architecture

### 2.1 Logical architecture

The Management Pi is the **master** and owns the schedule. Nothing transmits on the
LoRa medium unless the master invited it.

One cycle:

1. Master broadcasts a **POLL** naming the cycle and the expected repliers.
2. Each station transmits a **BEARING** in its assigned time slot.
3. Configuration deltas, when pending, are sent between cycles and explicitly
   acknowledged.

Stations never initiate, and are mutually deaf by construction (§18.2).

### 2.2 Hardware / platform architecture

| Node | Hardware | Attached |
|------|----------|----------|
| Management Pi | Raspberry Pi (`HornetManager`) | LoRa DTU (USB), `eth0` uplink, `wlan0` access point |
| Kraken Pi × 2 | Raspberry Pi | KrakenSDR (USB), LoRa DTU (USB), u-blox GNSS (USB), `wlan0`, `eth0` (wired management) |

Both carriers may be physically up at once. The LoRa DTUs are USB dongles
presenting a serial port; on the development bench they are reached over the
Universal Embedded Workbench as RFC2217 network serial ports, and on a Pi as a
local device node. The software treats both identically (§16).

**WLAN topology.** The Management Pi runs a `wlan0` access point on the
`192.168.50.0/24` subnet with its `eth0` as the uplink; the reproducible AP
configuration is §17.4. Stations associate to that access point when co-located,
receiving a `192.168.50.x` lease; this is the WLAN carrier. When stations are not
associated, the LoRa carrier is used. Each station also exposes a wired `eth0`
management path independent of `wlan0`.

**DTU device node.** The LoRa DTU is addressed by its `/dev/serial/by-id/` path
(`usb-1a86_USB_Single_Serial_<serial>-if00`), not a `ttyACM` index. The u-blox GNSS
receiver shares the `ttyACM` namespace and enumeration order is not stable across
boots (NFR-15.3).

Station count is a configuration property. The addressing scheme (§18) and the
slotted cycle (§5) accommodate additional stations without protocol change.

### 2.3 Software architecture

Three Python packages in this repository:

| Package | Runs on | Role |
|---------|---------|------|
| `hornethunter_shared` | both | wire contract, field registry, framing, geometry |
| `hornethunter_kraken` | Kraken Pi | station agent |
| `hornethunter_management` | Management Pi | master, UI, log aggregation |

Each Pi runs one long-lived systemd service. Configuration lives outside the
repository in `/etc/hornethunter/`.

**Persistence.** The Management Pi persists a per-station configuration mirror and
the previous known-good snapshot to disk (§7.6). Both nodes append structured logs
to local rotating files (§20). Nothing else is persisted in v1.

**A station agent shall not depend on the health of its KrakenSDR software.** It is
a separate process; it continues to answer polls and accept configuration pushes
when the DSP is stopped, wedged, or misconfigured (§13.4).

### 2.4 Component layering

Strict one-way dependency: **L0 Foundation → L1 Interfaces → L2 Application
logic**. The L0/L1 line is ownership — whether the protocol is implemented here.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ L2  Application logic                                                        │
│  ┌────────────┐ ┌───────────┐ ┌──────────────┐ ┌───────────┐ ┌────────────┐  │
│  │ Poll Cycle │ │ Transport │ │  Parameter   │ │  Link     │ │  Bearing   │  │
│  │ Scheduler  │ │ Selector  │ │ Distribution │ │  Health   │ │  Pipeline  │  │
│  │   §5       │ │    §6     │ │     §7       │ │   §8      │ │    §9      │  │
│  └────────────┘ └───────────┘ └──────────────┘ └───────────┘ └────────────┘  │
├──────────────────────────────────────────────────────────────────────────────┤
│ L1  Interfaces (wire logic implemented here)                                 │
│  ┌────────────┐ ┌───────────┐ ┌──────────────┐ ┌───────────┐ ┌────────────┐  │
│  │  HH-Link   │ │ DTU AT    │ │ Kraken DoA   │ │  Kraken   │ │ Management │  │
│  │ Frame+ARQ  │ │ Provision │ │   Source     │ │  Settings │ │  UI        │  │
│  │   §10      │ │   §11     │ │    §12       │ │    §13    │ │   §14      │  │
│  └────────────┘ └───────────┘ └──────────────┘ └───────────┘ └────────────┘  │
├──────────────────────────────────────────────────────────────────────────────┤
│ L0  Foundation (configured & used, not implemented here)                     │
│  ┌───────────────────────┐ ┌────────────────────────┐ ┌───────────────────┐  │
│  │ LoRa DTU & byte       │ │ krakensdr_doa +        │ │ Host platform     │  │
│  │ carriers   §15        │ │ middleware      §16    │ │        §17        │  │
│  └───────────────────────┘ └────────────────────────┘ └───────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Source layout mirrors the layers.** Each component is its own module; an
interface is never folded into its consumer. Lower layers never import higher ones;
where a foundation component notifies upward, it does so through a callback
registered at the composition root (the service entry point). Each L1 interface's
pure core — frame encode/decode, CRC, delta computation, canonical encoding,
state-machine transitions — is a set of free functions taking plain data, testable
at the fast tier with no hardware and no I/O (§24.1).

---

## 3. Implementation Phases

### 3.1 Phase 1 — Link foundation

**Scope.** The wire contract and its reliability, without any radio.

**Deliverables**

- `hornethunter_shared`: frame codec (§10.2), CRC, field registry (§7.2),
  canonical config encoding (§7.4), bearing record (§9.3).
- ARQ state machine (§10.5) and the byte-carrier abstraction (§15.2).
- Loopback and in-process link simulator; host-tier test suite.

**Exit criteria.** Frame codec round-trips all message types including
fragmentation; ARQ recovers from injected loss at 0/10/50 % rates; the entire suite
runs with no hardware attached.

### 3.2 Phase 2 — Real link and real Kraken

**Scope.** Phase 1 over the two physical DTUs and against the KrakenSDR software.

**Deliverables**

- DTU provisioning over AT (§11), poll cycle scheduler (§5), health evaluator (§8).
- Kraken DoA source with its three backends (§12.3) and settings client (§13).
- Parameter distribution end to end with divergence detection (§7).
- Management UI: numeric display, all parameter panels, log pane (§14).
- Structured logging on both nodes (§20).

**Exit criteria.** Both stations poll continuously over LoRa on the bench for one
hour with health reported and no unexplained gaps; a parameter change reaches a
station and is confirmed by read-back; a corrupted station configuration raises
`CONFIG_DIVERGED` and is repaired by one automatic full push.

### 3.3 Phase 3+ — Deferred

| Item | Note |
|------|------|
| Position fix (triangulation) | `geo.triangulate()` exists and is tested; unused in v1 (§9.6) |
| Graphical / map display | v1 is numeric only (N2) |
| Live GPS-driven heading | heading is a constant in v1 (§9.4) |
| Cross-station time alignment | not required until fixes exist (§9.5) |
| App-layer authentication | §22.3 |

---

## 4. Assumptions & Dependencies

### 4.1 Assumptions

- A1 — Two stations in v1, extensible by configuration.
- A2 — The operator sets all radio and array parameters manually; the software does
  not validate them for physical plausibility (§1.2).
- A3 — Station array **heading is fixed at 0°** by manual alignment and is not
  transmitted (§9.4).
- A4 — Station position is supplied by the KrakenSDR feed, may change, and is
  transmitted only when it changes (§9.4).
- A5 — No internet at runtime. All UI assets are served locally (§14.2).
- A6 — `AT+KEY`, if used, is a network separator and **not** a security control
  (§22.2).

### 4.2 Dependencies

| Dependency | Used for | Notes |
|------------|----------|-------|
| `krakensdr_doa` + its Node middleware | bearings and settings | see [krakensdr-integration.md](krakensdr-integration.md) |
| SX1262 LoRa DTU (transparent mode) | LoRa carrier | see [lora-dtu-sx1262.md](lora-dtu-sx1262.md) |
| `pyserial` | serial and RFC2217 carriers | RFC2217 used on the bench |
| Universal Embedded Workbench | bench tier | two DTUs on RFC2217 ports |

---

# Part A — Application Logic (L2)

## 5. Poll Cycle Scheduler

### 5.1 Purpose and scope

Owns the schedule on the shared medium. Runs on the Management Pi. Decides when to
poll, which stations are expected to answer, and how long to wait before declaring
a slot missed.

### 5.2 Requirements

- **FR-5.1** [Must] The scheduler shall broadcast one POLL per cycle addressed to
  the broadcast address, inviting all configured stations simultaneously.
- **FR-5.2** [Must] Each station shall be assigned a fixed reply slot index, and
  shall transmit its BEARING only within that slot.
- **FR-5.3** [Must] The cycle period shall be configurable, and the scheduler shall
  apply the configured period without imposing a policy limit of its own (§1.2).
- **FR-5.4** [Must] A slot with no valid BEARING by its deadline shall be recorded
  as a missed slot for that station and reported to §8.
- **FR-5.5** [Should] When a station misses its slot, the scheduler shall retry
  that station with a **unicast** POLL rather than re-broadcasting.
- **NFR-5.1** [Must] Cycle timing jitter shall be logged; the scheduler shall not
  silently drift.

### 5.3 Cycle structure

```
t=0     POLL (broadcast)          master transmits
t=g     ├─ slot 1  station 1 BEARING
t=g+s   └─ slot 2  station 2 BEARING
t=T     next cycle
```

`g` is a guard interval after the POLL, `s` the slot width, `T` the cycle period —
all configuration (§19). Slot width shall exceed the airtime of a maximum-size
BEARING frame plus the station's turnaround time. Measured airtimes for sizing are
in Appendix B.

### 5.4 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Slot empty | Missed slot recorded; unicast retry per FR-5.5; §8 informed |
| Frame arrives outside its slot | Accepted but flagged `late` in the log; slot still counted as filled |
| Frame from an unconfigured station | Discarded, counted, logged once per minute |
| Cycle overruns its period | Cycle truncated, overrun logged; next cycle starts on schedule |

---

## 6. Transport Selector

### 6.1 Purpose and scope

Chooses the carrier — WLAN or LoRa — **independently for each station**.

### 6.2 Requirements

- **FR-6.1** [Must] Carrier selection shall be per-station state, not a
  system-wide mode.
- **FR-6.2** [Must] The selector shall prefer WLAN when it is reachable and fall
  back to LoRa otherwise, automatically and without operator action.
- **FR-6.3** [Must] Promotion to WLAN shall require `promote_probes` consecutive
  successful probes; demotion to LoRa shall require `demote_probes` consecutive
  failures, with `demote_probes < promote_probes`.
- **FR-6.4** [Must] A station shall remain on a carrier for at least `dwell_s`
  after a switch before another switch is permitted.
- **FR-6.5** [Must] An operator shall be able to pin a station to a specific
  carrier, disabling automatic selection for that station.
- **FR-6.6** [Must] The active carrier shall be recorded on every bearing record
  and every log line (§20.3).
- **NFR-6.1** [Must] A carrier switch shall not lose a frame that was in flight;
  the pending ARQ transaction is retried on the new carrier.

### 6.3 Defaults

| Parameter | Default |
|-----------|---------|
| probe interval | 5 s |
| probe timeout | 1 s |
| `promote_probes` | 3 |
| `demote_probes` | 2 |
| `dwell_s` | 30 |

### 6.4 Behaviour on switch

Both carriers run the identical frame protocol (§10), so a switch changes only the
byte carrier; the codec, the ARQ state machine and the message set are unchanged.

- The health window **resets** on any carrier change (§8.4).
- The configured cycle period may differ per carrier; a change of update rate on
  switch is logged.

### 6.5 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Both carriers down | Station enters `LOST` (§8.3); polling continues on LoRa |
| WLAN reachable but station process dead | Probes fail → demote to LoRa → also fails → `LOST`. Distinguished in the log by probe-vs-frame failure |
| Carrier pinned and that carrier fails | No automatic fallback; station goes `LOST` and the UI states that a pin is in force |

---

## 7. Parameter Distribution & Configuration Integrity

### 7.1 Purpose and scope

Delivers the KrakenSDR configuration set from the Management Pi to the stations, and
continuously verifies that a station holds what the Management Pi holds for it. This
chapter owns the delta mechanism, the canonical CRC, and the divergence response.

### 7.2 Requirements

- **FR-7.1** [Must] Configuration changes shall be transmitted as **deltas** — only
  the fields that changed.
- **FR-7.2** [Must] Every field shall have a stable numeric identifier and a
  declared type, held in a shared **field registry** in `hornethunter_shared`.
- **FR-7.3** [Must] The Management Pi shall maintain a `config_version` per station,
  incremented on every accepted change.
- **FR-7.4** [Must] A **canonical CRC** over the operator-owned configuration shall
  be computed identically on both nodes (§7.4).
- **FR-7.5** [Must] Each station shall compute its CRC from the configuration **read
  back from the KrakenSDR software**, not from the delta it was asked to apply
  (§7.5).
- **FR-7.6** [Must] Every BEARING record shall carry the station's `config_version`
  and CRC.
- **FR-7.7** [Must] On a canonical-CRC mismatch the Management Pi shall raise
  `CONFIG_DIVERGED`, perform **exactly one** automatic full-set push, and set a
  sticky "resynced" marker on that station. If the mismatch persists after that
  push, it shall stop and require operator action. The CRC is the authoritative
  end-to-end check; `config_version` is the Management Pi's own mirror counter and
  the station's held version, not a value echoed across the link.
- **FR-7.8** [Must] The operator shall be able to request a full-set read from a
  station, and a full-set push to a station, as explicit manual operations.
- **FR-7.9** [Must] The Management Pi shall persist its per-station configuration
  mirror across restarts.
- **FR-7.10** [Should] A full-set encoding shall serialise only VFO slots up to
  `active_vfos`.
- **NFR-7.1** [Must] A single-field delta shall fit in one frame.

### 7.3 Field registry

The registry is data, not code: one entry per KrakenSDR settings key. It shall be
**generated from a live station's settings**. A live station carries **158 fields**,
including four per-VFO families (`vfo_demod_N`, `vfo_iq_N`, `vfo_squelch_mode_N`,
`vfo_fir_order_factor_N`) and `doa_decorrelation_method`; `en_fbavg` is absent.

| Column | Meaning |
|--------|---------|
| `id` | stable `u8`; never reused or renumbered |
| `key` | the `settings.json` key |
| `type` | wire type (`u8`,`u16`,`u32`,`i8`,`i16`,`i32`,`fixed`,`str`,`bool`,`enum`) |
| `scale` | fixed-point scale for real values |
| `crc_covered` | whether it participates in the canonical CRC (§7.4) |
| `restart_hint` | informational only; the KrakenSDR applies changes live (§13.3) |

A delta entry on the wire costs `1 B id + 1..4 B value` for scalars; a typical
three-field change is ~20 bytes — one frame (NFR-7.1).

### 7.4 Canonical CRC

The CRC is computed over canonically encoded field values, **not over the
`settings.json` file bytes** (the KrakenSDR software rewrites that file and float
re-serialisation is not byte-stable).

1. Take every registry entry with `crc_covered = true`, in ascending `id` order.
2. Encode each value in its declared fixed-width binary form; real values via their
   declared integer `scale`, so representation is identical on both nodes.
3. CRC-16/CCITT-FALSE over that byte sequence.

**Excluded from `crc_covered`:** any field the KrakenSDR software mutates on its own
— live position and heading when position is GPS-sourced, plus bookkeeping
(`ext_upd_flag`, `timestamp`).

### 7.5 Divergence detection

```
management                                     station
   │ delta(fields, version=N, crc=C) ─────────────►│
   │                                               │ apply via §13
   │                                               │ read back via §13
   │                                               │ crc' = canonical(read-back)
   │◄──────────── ACK(version=N, crc=crc') ────────│
   │ crc' == C ?  yes → in sync
   │              no  → CONFIG_DIVERGED
   │
   │◄──── BEARING(version, crc) ──── every cycle ──│   continuous re-verification
```

The CRC is computed from the read-back, so the check catches the KrakenSDR clamping
a value, rejecting it, or its settings watcher never observing the write. The CRC
rides on every bearing, so a station whose configuration is changed locally is
detected within one cycle.

### 7.6 Revert and recovery

The Management Pi retains the previous accepted configuration snapshot per station.
With the station agent's independence from DSP health (§2.3), every exposed setting
is recoverable: a setting that breaks the KrakenSDR does not break the command
channel that undoes it.

### 7.7 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Delta ACKed, CRC mismatch | `CONFIG_DIVERGED`; one automatic full push; sticky marker (FR-7.7) |
| Mismatch persists after full push | Stop. `CONFIG_DIVERGED` latched; operator action required |
| Station unknown to the mirror (first contact) | No baseline for deltas; a manual full read is required (FR-7.8) |
| Delta not ACKed within ARQ budget | Change stays pending; version not advanced; §8 reports the link fault |
| Read-back fails (KrakenSDR unreachable) | ACK carries a `kraken_down` flag; CRC not asserted; distinguished from divergence |

---

## 8. Link Health Evaluator

### 8.1 Purpose and scope

Converts link behaviour into a single per-station indicator and into metrics for
later audit.

**Health is derived from retransmission behaviour only.** Signal strength is
displayed as information but does not contribute to the indicator.

### 8.2 Requirements

- **NFR-8.1** [Must] Health shall be computed **exclusively** from retransmission
  and delivery outcomes. RSSI shall not contribute.
- **FR-8.1** [Must] Health shall be evaluated over a rolling window of the last
  `window_cycles` cycles, per station.
- **FR-8.2** [Must] The indicator shall be `GREEN` when no retransmission occurred
  in the window.
- **FR-8.3** [Must] The indicator shall be `ORANGE` when retransmissions occurred
  but every cycle was ultimately delivered and the retry rate is at or below
  `retry_rate_threshold`.
- **FR-8.4** [Must] The indicator shall be `RED` when ARQ was exhausted on any
  cycle, **or** the retry rate exceeds `retry_rate_threshold`, **or** no bearing has
  been received for `stale_cycles` consecutive cycles.
- **FR-8.5** [Must] The window shall be **reset** on any carrier change (§6.4).
- **FR-8.6** [Must] `CONFIG_DIVERGED` (§7.5) shall be indicated **independently** of
  link health, not folded into it.
- **FR-8.7** [Must] The evaluator shall expose retry count, retry rate, consecutive
  misses, round-trip time and last-received RSSI for display and logging.
- **NFR-8.2** [Must] Thresholds and window length shall be configurable at runtime.

### 8.3 States

| State | Condition | Meaning |
|-------|-----------|---------|
| 🟢 `GREEN` | zero retransmissions in window | delivering first-try |
| 🟠 `ORANGE` | retries present, all cycles delivered, rate ≤ threshold | degraded, still working |
| 🔴 `RED` | ARQ exhausted, or rate > threshold, or `stale_cycles` consecutive misses | operator action required |
| ⚫ `LOST` | no traffic on either carrier | terminal case of `RED`; §6.5 |
| 🟣 `CONFIG_DIVERGED` | version/CRC mismatch | independent axis; wrong-data fault, not a link fault |

The reliability requirement is: *no station shall enter `ORANGE` during normal
operation.*

### 8.4 Defaults

| Parameter | Default |
|-----------|---------|
| `window_cycles` | 20 |
| `retry_rate_threshold` | 20 % (more than 4 of 20 cycles needing a retransmission) |
| `stale_cycles` | 5 |

Thresholds and window length are runtime configuration (NFR-8.2).

### 8.5 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Window not yet full after start or reset | State reported as `GREEN` with an explicit `warming_up` qualifier; thresholds not applied until the window fills |
| Station on WLAN | Retries are near-zero, so health reads `GREEN` regardless of RF conditions. The indicator is **always displayed with its carrier** (FR-6.6); `GREEN` on WLAN makes no claim about the LoRa link |

---

## 9. Bearing Pipeline

### 9.1 Purpose and scope

Runs on the station. Converts the KrakenSDR measurement stream into the compact
record transmitted once per cycle.

### 9.2 Requirements

- **FR-9.1** [Must] The station shall subscribe to the DoA stream (§12) and retain
  the most recent measurement.
- **FR-9.2** [Must] On being polled, the station shall transmit the **latest**
  measurement, not an average or a batch.
- **FR-9.3** [Must] Each record shall carry the measurement's **age in
  milliseconds** at the moment of transmission.
- **FR-9.4** [Must] The station shall report how many measurements were produced
  and discarded since the previous poll.
- **FR-9.5** [Must] Station position shall be transmitted **only when it has
  changed** beyond `position_epsilon` since the last transmitted position.
- **FR-9.6** [Must] Records shall indicate whether the KrakenSDR feed is live, and
  shall be transmitted with a `no_data` indication when it is not.
- **NFR-9.1** [Must] A record without position shall not exceed 10 bytes of payload.

### 9.3 Record

| Field | Type | Bytes | Notes |
|-------|------|------:|-------|
| `flags` | `u8` | 1 | see below |
| `bearing_cdeg` | `u16` | 2 | centi-degrees, 0..35999 |
| `confidence` | `u8` | 1 | quantised from the feed's `conf` (§9.4) |
| `power_dbm` | `i8` | 1 | from the feed's `power` |
| `age_ms` | `u16` | 2 | measurement age at transmission (FR-9.3) |
| `config_version` | `u8` | 1 | §7.6 |
| `config_crc` | `u16` | 2 | §7.4 |
| `dlat`,`dlon` | `i16`×2 | +4 | decimetres from station reference; present only per FR-9.5 |

**10 bytes** normally, 14 with position. Flags: position-present, position-source,
kraken-link-up, squelch-open, adc-overdrive, no-data, reserved ×2.

The feed's `conf` is **not normalised to 0..1** (values above 100 are observed, e.g.
159); the station quantises it into `confidence` `u8` by clamping. `adc_overdrive`
and `squelch_open` come from the feed and are carried so the receiver can interpret
a suspicious bearing; the station does not act on them.

**`age_ms` floor.** The feed reports a measurement `latency` and `processing_time`
totalling ~0.4–0.6 s, so `age_ms` has a floor near half a second even for the
newest measurement.

### 9.4 Position and heading

- **Position** is taken from the KrakenSDR feed, may change, and is transmitted on
  change (FR-9.5) against a per-station reference position, in decimetres.
  Stationary GPS position jitter of ~3–4 m is observed, so `position_epsilon_dm`
  defaults to 50 (5 m). Re-basing the reference is a §7 configuration operation.
- **Heading** is a fixed 0° by manual array alignment (A3) and is **not transmitted**
  in v1.

A station whose array is physically rotated away from its assumed heading produces
bearings wrong by that angle, and no link or confidence metric reveals it. Alignment
and its verification are operator responsibilities (§1.2, §23.2).

### 9.5 No clocks

v1 produces no position fix, so no cross-station time alignment is required. Records
carry **age**, not an absolute timestamp; the Management Pi converts to absolute
time on arrival using its own clock. No clock synchronisation exists in v1.

### 9.6 Deferred: fixes

`hornethunter_shared.geo.triangulate()` and `intersect_bearings()` are implemented
and unit-tested, and `management_pi` exposes a `--fix-from` entry point. These are
**v2 seams, unused in v1**.

### 9.7 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| No DoA measurement ever received | Record sent with `no_data`; station still answers polls |
| Feed stalls mid-operation | `kraken_link_up` cleared, `age_ms` grows; ages beyond `max_age_ms` reported as `no_data` |
| `age_ms` would overflow `u16` (>65.5 s) | Clamped to `0xFFFF` and `no_data` set |
| Bearing outside 0..359.99° | Discarded, counted, logged; previous measurement retained |
| Station moved beyond ±3.2 km of its reference | Reference re-base required; flagged to the operator |

---

# Part B — Interfaces (L1)

## 10. HH-Link Frame Format & ARQ

### 10.1 Purpose and peer

The protocol between the Management Pi and the stations. Its peer is another HH-Link
endpoint. **One frame format runs over both carriers** — LoRa serial and WLAN TCP.
JSON is used in logs and tests; it is never the wire form.

### 10.2 Frame format

The LoRa carrier is a transparent byte pipe with **no frame boundaries**: the DTU
packetises on UART idle gaps, so one write may arrive split or coalesced. Frames are
self-delimiting.

```
┌────────┬─────────┬──────┬─────┬─────┬─────┬───────────┬───────┐
│ SYNC   │ VER/TYPE│ DEST │ SRC │ SEQ │ LEN │  PAYLOAD  │ CRC16 │
│ 2 B    │  1 B    │ 1 B  │ 1 B │ 1 B │ 1 B │ 0..200 B  │  2 B  │
└────────┴─────────┴──────┴─────┴─────┴─────┴───────────┴───────┘
   A5 5A   ver:4     addr   addr   0..   len   payload    CCITT
           type:4                  255                   over VER..PAYLOAD
```

- **9 bytes** of overhead. Payload capped at **200 B**, so a frame never exceeds
  209 B and always fits a single DTU packet (Appendix A).
- **The DTU appends an RSSI byte after the CRC** when RSSI reporting is enabled
  (§11.3). The receiver shall strip that trailing byte **before** validating the CRC.
- CRC-16/CCITT-FALSE, over VER..PAYLOAD. The LoRa PHY guarantees integrity (§15.3);
  this CRC guards against framing errors — mis-synchronisation, truncation,
  coalescing.

### 10.3 Message types

| Type | Name | Direction | ACKed | Payload |
|-----:|------|-----------|-------|---------|
| 0x1 | `POLL` | master → broadcast or unicast | implicitly, by the reply | cycle seq, slot width, expected-station bitmap |
| 0x2 | `BEARING` | station → master | — (it *is* the poll response) | §9.3 record |
| 0x3 | `ACK` | either | — | acked seq, config version, config CRC, status flags |
| 0x4 | `PARAM_DELTA` | master → station | yes | changed `(id, value)` entries |
| 0x5 | `PARAM_FULL` | master → station | yes | full set; fragmented |
| 0x6 | `PARAM_REQ` | master → station | yes | request full-set report |
| 0x7 | `PARAM_REPORT` | station → master | yes | full set; fragmented |
| 0x8 | `IDENT` | station → master | yes | agent version, schema version, capabilities |

Fragmented types carry `frag_index` and `frag_total` at the head of the payload.

### 10.4 Requirements

- **FR-10.1** [Must] Frames shall be self-delimiting by sync word, length and CRC,
  and the receiver shall recover from arbitrary garbage without operator action.
- **FR-10.2** [Must] The identical frame format and ARQ logic shall be used over
  both carriers.
- **FR-10.3** [Must] Payload shall not exceed 200 bytes; larger messages shall be
  fragmented at the HH-Link layer (FR-10.6).
- **FR-10.4** [Must] The receiver shall strip a carrier-appended RSSI byte before
  CRC validation when RSSI reporting is enabled.
- **FR-10.5** [Must] Frames failing CRC shall be discarded and counted, never
  partially interpreted.
- **FR-10.6** [Must] `PARAM_FULL` and `PARAM_REPORT` shall support fragmentation
  with per-fragment acknowledgement.
- **FR-10.7** [Must] Duplicate frames — a retransmission whose ACK was lost — shall
  be detected by sequence number, acknowledged again, and **applied only once**.
- **NFR-10.1** [Must] The codec shall be a pure function of bytes, independent of
  carrier and of wall-clock time.

### 10.5 ARQ

**Stop-and-wait**, one outstanding frame per direction per station.

| Parameter | Default |
|-----------|---------|
| retransmission timeout | 400 ms |
| maximum attempts | 3 (original plus two retransmissions) |
| sequence space | `u8`, wrapping |

The carrier discards frames that fail their own PHY CRC (§15.3), so this link loses
frames but never delivers corrupted ones; sequence-plus-ACK-plus-retransmit is
sufficient. On exhausting attempts the frame is abandoned, the failure is reported
to §8, and no automatic recovery is attempted (N4).

### 10.6 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Partial frame received | Retained in the reassembly buffer until complete or `frame_timeout` elapses, then discarded and counted |
| Garbage / lost sync | Byte-wise resynchronisation on the sync word; discarded bytes counted |
| Coalesced frames in one read | All complete frames extracted from the buffer in order |
| CRC failure | Discarded and counted (FR-10.5); ARQ handles the retransmission |
| Duplicate delivery | Re-acknowledged, applied once (FR-10.7) |
| Fragment set incomplete | Whole set discarded after `frag_timeout`; sender retries the set |

---

## 11. LoRa DTU Provisioning Interface

### 11.1 Purpose and peer

Configures a locally attached SX1262 DTU over its AT command set, so that a node's
radio settings come from its configuration file.

Entering AT mode requires the escape terminated with CRLF — `+++\r\n`; a bare `+++`
produces no response. Verified on firmware Ver1.2; see
[lora-dtu-sx1262.md](lora-dtu-sx1262.md).

### 11.2 Requirements

- **FR-11.1** [Must] On startup the agent shall read the DTU's current parameters
  and compare them against its configuration.
- **FR-11.2** [Must] The agent shall write only parameters that differ, then leave
  AT mode with `AT+EXIT` so settings take effect.
- **FR-11.3** [Must] The agent shall verify by read-back every parameter it wrote,
  except write-only parameters (§11.4).
- **FR-11.4** [Must] Entering AT mode shall use `+++\r\n`; every command shall be
  CRLF-terminated.
- **FR-11.5** [Must] The agent shall guarantee `AT+EXIT` on every exit path,
  including on error, so a DTU is never left in AT mode.
- **FR-11.6** [Must] Radio parameters shall be applied exactly as configured, with
  no plausibility checking (§1.2, A2).
- **FR-11.7** [Should] Parameters shall be queried individually rather than parsed
  from `AT+AllP?`, whose field order differs between the vendor documentation and
  the shipped firmware.
- **NFR-11.1** [Must] Provisioning shall be idempotent and safe to re-run.

### 11.3 Parameters applied

| Command | Purpose | Note |
|---------|---------|------|
| `AT+MODE` | operating mode | `1` (transparent/stream) — required by §15.1 |
| `AT+ADDR` | node address | per §18.2 |
| `AT+TXCH`,`AT+RXCH` | channel | operator-owned (§1.2) |
| `AT+SF`,`AT+BW`,`AT+CR`,`AT+PWR` | radio parameters | operator-owned. `AT+BW` takes an **index** (`0`=125 kHz), not a value in kHz. `AT+PWR` range 10–22 dBm |
| `AT+LBT` | listen-before-talk | `0`; the master owns the schedule (§2.1) |
| `AT+RSSI` | append RSSI to received data | configurable; display-only (NFR-8.1). Affects framing (FR-10.4) |
| `AT+KEY` | AES key | **write-only**; cannot be read back (FR-11.3). Not a security control (§22.2) |

### 11.4 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| AT mode not entered | Provisioning abandoned; DTU left untouched and assumed already configured; logged as a startup warning; service continues |
| Read-back mismatch | Retried once, then logged as an error; the node continues with the actual value and reports it |
| `AT+KEY` set | Cannot be verified. Recorded as "written, unverifiable" |
| DTU absent | LoRa carrier marked unavailable; WLAN-only operation; not fatal |
| Exit fails | Retried, then `AT+REBOOT` (present in firmware, absent from vendor documentation) |

---

## 12. Kraken DoA Source Interface

### 12.1 Purpose and peer

Consumes direction-of-arrival measurements from the KrakenSDR software on the same
host. Contract detail is in
[krakensdr-integration.md](krakensdr-integration.md).

### 12.2 Protocol

With `doa_data_format = "Kraken Pro Local"`, the DSP posts every measurement to its
Node middleware, which broadcasts it to all WebSocket clients on port **8021**. The
station **subscribes and is pushed to**; it does not poll.

```
ws://127.0.0.1:8021   →   one JSON object per measurement
```

The station requires `doa_data_format = Kraken Pro Local`; other formats do not
provide this local push feed. On the deployed station `doa_data_format` is
`Full POST` and port 8021 is not served, so the read path is confirmed once
`Kraken Pro Local` is set at commissioning (§23.2).

Fields consumed: `station_id`, `tStamp`, `radioBearing`, `conf`, `power`, `freq`,
`latitude`, `longitude`, `speed`, `latency`, `processing_time`, `adc_overdrive`,
`num_corr_sources`, `snr`. `conf` is not normalised to 0..1 (§9.3). `doaArray` (the
full spectrum) is **not consumed in v1**.

### 12.3 Backends

One internal measurement type, three sources, selected by configuration:

| Backend | Use | Transport |
|---------|-----|-----------|
| `kraken` | real hardware | WebSocket, port 8021 |
| `simulator` | `KrakenSimulator` | HTTP `GET /api/v1/doa` |
| `synthetic` | host-tier tests, no hardware | in-process generator |

The simulator and the real software use different field names and transports; the
adapter maps between them:

| | simulator | real |
|---|---|---|
| bearing | `bearing_deg` | `radioBearing` |
| quality | `width_rad` | `conf` |
| power | `rssi_dbfs` | `power` |
| frequency | `center_freq_hz` | `freq` |
| transport | HTTP poll | WebSocket push |

### 12.4 Requirements

- **FR-12.1** [Must] The station shall maintain a subscription to the DoA feed and
  reconnect automatically with backoff when it drops.
- **FR-12.2** [Must] Feed availability shall be exposed as an explicit state, and
  reported in every bearing record (FR-9.6).
- **FR-12.3** [Must] All three backends shall present one internal type; no caller
  shall branch on backend.
- **FR-12.4** [Must] Malformed or unparseable records shall be discarded and
  counted, never propagated as bearings.
- **FR-12.5** [Should] The agent shall ensure `doa_data_format` is
  `Kraken Pro Local` at startup, since any other value silences the feed.
- **NFR-12.1** [Must] Adapter mapping shall be pure and table-driven, testable
  without a network.

### 12.5 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| WebSocket refuses connection | Retry with capped exponential backoff; feed state down; polls still answered |
| Connection drops mid-stream | As above; last measurement retained and ages out per §9.7 |
| Malformed JSON, or missing required field | Record discarded and counted (FR-12.4) |
| `doa_data_format` not `Kraken Pro Local` | Feed silent. Detected as feed-down; §7 read-back reveals the cause |
| Feed faster than the poll cycle | Latest wins; discard count reported (FR-9.4) |

---

## 13. Kraken Settings Interface

### 13.1 Purpose and peer

Reads and writes the KrakenSDR configuration on the local host.

**As deployed, a station can be read remotely but not written.** The routes:

```
GET  http://127.0.0.1:8081/settings.json                  works (read-only)
POST http://127.0.0.1:8081/upload?path=/   (multipart)    404
GET  http://127.0.0.1:8042/settings                       404
POST http://127.0.0.1:8042/settings                       404
```

- **8081 is read-only by configuration.** `gui_run.sh` adds miniserve's `-u`
  (`--upload-files`) flag only when `en_remote_control` is true in `settings.json`;
  it is false on the deployed station. `en_remote_control` can only be changed
  locally, so enabling remote writing is a **commissioning prerequisite** (§23.2).
- **8042 lacks the `/settings` route.** Port 8042 is the Express middleware and is
  running, but the installed build predates that route.

The interface is **route-agnostic**: one internal read/write contract, with
implementations for both routes, probed at startup to discover which the local
station provides.

### 13.2 Requirements

- **FR-13.1** [Must] The agent shall apply a delta by reading current settings,
  merging the changed fields, and writing the merged result back.
- **FR-13.5** [Must] The agent shall support both write routes behind one internal
  contract, and shall determine at startup which the local station provides.
- **FR-13.6** [Must] When no write route is available, the agent shall report the
  station as **read-only** rather than failing, shall still serve configuration
  read-back for divergence detection (§7.5), and shall reject parameter pushes with
  a distinct reason the UI can display.
- **FR-13.2** [Must] After every write the agent shall re-read settings and compute
  the canonical CRC from that read-back (FR-7.5).
- **FR-13.3** [Must] The agent shall report every field the KrakenSDR altered,
  clamped, or ignored relative to what was requested.
- **FR-13.4** [Must] The agent shall remain fully operational when this interface is
  unreachable (§2.3).
- **NFR-13.1** [Must] A parameter application shall complete within
  `param_apply_timeout_s` or be reported as failed, never left indeterminate.

### 13.3 Application is live

The KrakenSDR software watches its settings file on a **0.5 s timer** and applies
changes in place, including retuning the receiver when the centre frequency changes.
There is no service restart. The parameter push is an ordinary operation with no
"station is blind" state in the UI.

### 13.4 All settings are exposed

Every field is operator-editable (§14.3). This is safe because:

- the station agent is independent of KrakenSDR health (§2.3), so no setting can
  take away the channel needed to undo it;
- the Management Pi retains the previous accepted snapshot (§7.6).

The UI **warns** on fields the system depends on — `doa_data_format` (silences the
feed), `default_ip` and `data_interface` (break the DAQ chain) — but does not
prevent their change.

### 13.5 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Endpoint unreachable | Delta not applied; ACK carries `kraken_down`; CRC not asserted; distinguished from divergence (§7.7) |
| Write accepted but read-back differs | Reported per FR-13.3; surfaces as `CONFIG_DIVERGED` at the Management Pi |
| No write route available | Station reported read-only (FR-13.6); reads still served |
| Malformed settings written | The API performs no schema validation. Mitigated by revert (§7.6) |

---

## 14. Management UI Interface

### 14.1 Purpose and peer

A browser-facing interface on the Management Pi. Its peer is the operator.

### 14.2 Protocol and assets

- HTTP for the page and for operator actions; a **WebSocket** pushes live values.
- **All assets are served locally** (A5). v1 displays numbers only (N2), so no
  charting or mapping library is required.

### 14.3 Requirements

- **FR-14.1** [Must] The UI shall display, per station: bearing, confidence, power,
  measurement age, discard count, active carrier, health state, retry count and
  rate, round-trip time, last RSSI, `config_version` and configuration state.
- **FR-14.2** [Must] Values shall be **numeric**; v1 shall provide no graphical
  bearing display, plot, or map.
- **FR-14.3** [Must] The UI shall expose **every** KrakenSDR settings field,
  organised into panels mirroring the KrakenSDR software's own grouping.
- **FR-14.4** [Must] The UI shall warn on fields the system depends on (§13.4)
  without preventing their modification.
- **FR-14.5** [Must] Editing a field shall transmit a delta (FR-7.1), never a full
  set.
- **FR-14.6** [Must] The UI shall provide explicit *Read full settings* and *Push
  full settings* actions per station (FR-7.8).
- **FR-14.7** [Must] The UI shall provide a *revert to last known good* action per
  station (§7.6).
- **FR-14.8** [Must] The UI shall provide a per-station carrier pin control (FR-6.5)
  and show when a pin is in force.
- **FR-14.9** [Must] The UI shall display a live tail of the debugging log (§20).
- **FR-14.10** [Must] Health and configuration state shall be shown as **separate**
  indicators (FR-8.6), each labelled with the carrier it refers to (§8.5).
- **NFR-14.1** [Should] Displayed values shall update within 250 ms of arrival.

### 14.4 Panels

Grouped as the KrakenSDR software groups them; its documentation is vendored at
[krakensdr-wiki/](krakensdr-wiki/).

| Panel | Content |
|-------|---------|
| Stations | live numeric rows, health, carrier, configuration state |
| RF Receiver | `center_freq`, `uniform_gain`, `data_interface`, `default_ip` |
| DoA Configuration | `en_doa`, `ant_arrangement`, `ula_direction`, `ant_spacing_meters`, `custom_array_x/y_meters`, `array_offset`, `doa_method`, `doa_decorrelation_method`, `expected_num_of_sources` |
| Display Options | `doa_fig_type`, `en_peak_hold`, `compass_offset` |
| VFO Configuration | `spectrum_calculation`, `vfo_mode`, `active_vfos`, `output_vfo`, `dsp_decimation`, `en_optimize_short_bursts` |
| VFO 0–15 | `vfo_freq_N`, `vfo_bw_N`, `vfo_squelch_N`, `vfo_squelch_mode_N`, `vfo_demod_N`, `vfo_iq_N`, `vfo_fir_order_factor_N` |
| Station Information | `station_id`, `location_source`, `latitude`, `longitude`, `heading`, `doa_data_format`, `krakenpro_key`, `rdf_mapper_server` |
| Recording / System | `en_data_record`, `write_interval`, `logging_level`, `en_hw_check`, `disable_tooltips` |
| Link | carrier pin, health thresholds, cycle period, log tail |

Field types, units and ranges come from the field registry (§7.3), so the form is
generated from data. `center_freq` is in **MHz** while `vfo_freq_N` is in **Hz**.

### 14.5 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| WebSocket drops | Client reconnects; values marked stale rather than frozen at last value |
| Station unreachable | Row retained showing last values, greyed, with age and health state |
| Operator submits an out-of-type value | Rejected client-side against the registry type before transmission |
| Two operators editing at once | Last write wins; both changes appear in the log. Not defended against in v1 |

---

# Part C — Foundation (L0)

## 15. LoRa DTU & Byte Carriers

### 15.1 Purpose and division of responsibility

The SX1262 DTU is a vendor device used as a **transparent byte pipe** (`AT+MODE=1`).
We configure it (§11) and rely on its documented behaviour.

| We own | The device owns |
|--------|-----------------|
| Framing, addressing, reliability, scheduling | Modulation, PHY CRC, packetisation, AES, caching |

### 15.2 Carrier abstraction

Three byte carriers behind one interface, so nothing above §10 knows which is in use:

| Carrier | Used for |
|---------|----------|
| local serial device | LoRa on a Pi |
| RFC2217 network serial | LoRa on the development bench |
| TCP socket | WLAN |

### 15.3 Properties relied upon

- **Payload CRC cannot be disabled**, and a packet failing it is dropped rather than
  delivered. The link **loses frames but never corrupts them** (§10.5).
- **Maximum single packet is 240 bytes**; larger writes are auto-packetised. Frames
  are capped at 209 B (§10.2) to stay inside one packet.
- **Transparent mode has no local echo** — bytes written to a DTU do not return on
  that port. Diagnostics read the *peer*.
- **First-packet warm-up loss**: the first one or two transmissions after opening a
  fresh connection may be dropped. Startup shall send and discard a throwaway frame.
- 960-byte internal cache and auto-packetisation are fixed and not configurable.

### 15.4 Requirements

- **NFR-15.1** [Must] No component above §10 shall depend on which carrier is in use.
- **NFR-15.2** [Must] Startup shall absorb warm-up loss before the first real frame.
- **NFR-15.3** [Must] Resolution of a serial device shall be by stable identity — a
  `/dev/serial/by-id/` path on a Pi (§2.2), or a workbench slot label on the bench —
  not by `ttyACM` index, which is not stable across enumerations.

### 15.5 Failure modes

Exercised **transitively** through §10 and §11 — this layer has no tests of its own.

| Condition | Behaviour |
|-----------|-----------|
| Port disappears (unplug) | Carrier marked down; reopen with backoff; WLAN continues (§6) |
| RFC2217 proxy already has a client | One client per port only. Reported as carrier-unavailable |
| Device left in AT mode | Data does not flow. §11.4 exit guarantee prevents; recovery is `AT+REBOOT` or power cycle |

---

## 16. krakensdr_doa & Middleware

### 16.1 Purpose and division of responsibility

External software on the station host. We configure it and consume its outputs.

| We own | It owns |
|--------|---------|
| Which settings we write; how we parse its outputs | DSP, DoA estimation, its settings watcher, its middleware |

### 16.2 Lifecycle and gating

Our agent and this software start independently. **The agent shall not gate its own
startup on the software being present or healthy** (§2.3, FR-13.4): it starts,
answers polls, and reports the feed as down.

### 16.3 Requirements

- **NFR-16.1** [Must] The agent shall tolerate this software being absent, stopped,
  restarted, or misconfigured at any time, without the agent restarting.
- **NFR-16.2** [Must] Version-sensitive coupling shall be confined to §12 and §13.

### 16.4 Failure modes

Exercised transitively through §12 and §13. Relied-upon upstream behaviours — the
middleware ports, the `Kraken Pro Local` fan-out, the 0.5 s settings watcher — are
covered by the simulator backend.

---

## 17. Host Platform

### 17.1 Purpose and division of responsibility

Raspberry Pi OS, systemd, the network stack, the filesystem, and Python. We
configure units, paths and dependencies.

### 17.2 Requirements

- **NFR-17.1** [Must] Each node shall run as a single systemd service, restarting
  automatically on failure.
- **NFR-17.2** [Must] Configuration shall live outside the repository, in
  `/etc/hornethunter/`.
- **NFR-17.3** [Must] Logs shall be written to a local path with size-bounded
  rotation (§20.4).
- **NFR-17.4** [Must] A node shall reach a working state after an unattended power
  cycle with no operator action.

### 17.3 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Service crash | systemd restarts; restart counted and logged; health window resets |
| Disk full | Rotation bounds log growth (§20.4); logging degrades before operation does |
| Clock jumps (no NTP) | Tolerated: v1 uses no absolute cross-node time (§9.5). Log timestamps record monotonic time alongside wall-clock |

### 17.4 Access-point configuration

The Management Pi provides the field WLAN as a NetworkManager access point on
`wlan0`, with `eth0` as the uplink. NetworkManager supplies both the access point
(its hostapd backend) and DHCP/DNS (its internal dnsmasq) via `ipv4.method shared`;
no `hostapd` or `dnsmasq` packages are installed. Verified platform: Debian 13
(trixie), NetworkManager 1.52.

- **NFR-17.5** [Must] The Management Pi shall run a NetworkManager AP-mode
  connection on `wlan0` with `ipv4.method shared`, autostarting at boot.
- **NFR-17.6** [Must] Each station shall associate to that SSID via a
  NetworkManager client connection, autostarting at boot.
- **NFR-17.7** [Must] The access-point subnet shall not overlap any uplink subnet.

| Parameter | Value |
|-----------|-------|
| interface | `wlan0` access point, `eth0` uplink |
| mechanism | NetworkManager connection, `802-11-wireless.mode ap`, `ipv4.method shared` |
| AP address / subnet | `192.168.50.1/24` |
| station leases | NetworkManager shared DHCP, `192.168.50.0/24` |
| band | 2.4 GHz (`bg`) |
| channel | operator-chosen (NetworkManager auto by default) |
| SSID / passphrase | operator-set, WPA2-PSK (`wpa-psk`) |

**Reproducible setup — Management Pi (access point):**

```bash
nmcli con add type wifi ifname wlan0 con-name hh-ap ssid "<SSID>" autoconnect yes
nmcli con modify hh-ap \
  802-11-wireless.mode ap 802-11-wireless.band bg \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<passphrase>" \
  ipv4.method shared ipv4.addresses 192.168.50.1/24 \
  connection.autoconnect-priority 100
nmcli con up hh-ap
```

**Reproducible setup — each station (associate to the access point):**

```bash
nmcli con add type wifi ifname wlan0 con-name hh-field ssid "<SSID>" autoconnect yes
nmcli con modify hh-field wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<passphrase>"
nmcli con up hh-field
```

Stations then receive a `192.168.50.x` lease, which the WLAN carrier (§6, §15.2)
uses when associated. The wired `eth0` path (§2.2) remains available regardless of
`wlan0` state.

---

# Part D — Cross-cutting Concerns

## 18. Identity & Addressing

### 18.1 Requirements

- **FR-18.1** [Must] Each node shall have a stable identity: a station id used in
  the protocol and a human-readable name used in the UI and logs.
- **FR-18.2** [Must] HH-Link addresses shall be independent of DTU hardware
  addresses, so a dongle can be swapped without a protocol change.
- **FR-18.3** [Must] The Management Pi shall reject and count frames from
  unconfigured station ids (§5.4).

### 18.2 DTU addressing

Transparent mode is **group-addressed, not a flat broadcast**: a receiver accepts a
frame only when both address and channel match the sender — except address `0xFFFF`,
which receives from all addresses on its channel and whose transmissions reach all
of them.

| Node | DTU `AT+ADDR` | Consequence |
|------|---------------|-------------|
| Management Pi | `0xFFFF` | its polls reach every station; it hears every station |
| Station *n* | `0x0001`, `0x0002`, … | stations are **mutually deaf** |

A broadcast poll reaches all stations in one transmission (§5.3), and no station
processes another's reply.

---

## 19. Configuration Catalog

### 19.1 Requirements

- **FR-19.1** [Must] All tunables shall be declared in configuration with documented
  defaults; none shall be hard-coded at a call site.
- **FR-19.2** [Must] Health thresholds, window length and cycle period shall be
  changeable at runtime without restart (NFR-8.2).
- **FR-19.3** [Must] Radio parameters shall be applied verbatim, without
  plausibility checking (A2).

### 19.2 Catalog

| Group | Keys |
|-------|------|
| identity | `station.id`, `station.name`, `station.slot_index` |
| link | `link.address`, `link.channel`, `link.sf`, `link.bw`, `link.cr`, `link.power`, `link.lbt`, `link.rssi_append`, `link.key` |
| carrier | `carrier.serial_url`, `carrier.tcp_endpoint`, `carrier.probe_interval_s`, `carrier.probe_timeout_s`, `carrier.promote_probes`, `carrier.demote_probes`, `carrier.dwell_s`, `carrier.pin` |
| cycle | `cycle.period_ms`, `cycle.guard_ms`, `cycle.slot_ms` |
| arq | `arq.timeout_ms`, `arq.max_attempts`, `arq.frame_timeout_ms`, `arq.frag_timeout_ms` |
| health | `health.window_cycles`, `health.retry_rate_threshold`, `health.stale_cycles` |
| kraken | `kraken.backend`, `kraken.ws_url`, `kraken.settings_url`, `kraken.param_apply_timeout_s` |
| bearing | `bearing.position_epsilon_dm`, `bearing.max_age_ms`, `bearing.reference_lat`, `bearing.reference_lon` |
| logging | `log.path`, `log.max_bytes`, `log.backup_count`, `log.level` |
| ui | `ui.listen`, `ui.port` |

Defaults are in Appendix C.

---

## 20. Logging & Observability

### 20.1 Purpose

The log shall be sufficient to reconstruct after the fact why the link or the Kraken
interface behaved as it did.

### 20.2 Requirements

- **FR-20.1** [Must] Each node shall write structured **JSONL**, one object per
  event, machine-parseable without regular expressions.
- **FR-20.2** [Must] Every transmitted and received frame shall be logged with
  direction, type, source, destination, sequence, length, attempt number, round-trip
  time, CRC result, carrier, and RSSI when available.
- **FR-20.3** [Must] Every health state transition, ARQ exhaustion, carrier switch
  and health-window reset shall be logged with its cause.
- **FR-20.4** [Must] Every parameter operation shall be logged with the fields
  changed, `config_version`, expected and observed CRC, and read-back differences.
- **FR-20.5** [Must] Kraken feed connects, drops, malformed records, discard counts
  and `adc_overdrive` transitions shall be logged.
- **FR-20.6** [Must] **Logs shall never be transmitted over the LoRa carrier.**
- **FR-20.7** [Must] Every record shall carry both wall-clock and monotonic
  timestamps (§17.3).
- **NFR-20.1** [Must] Logging shall never block the poll cycle; it shall drop
  records and count the drops in preference to stalling.

### 20.3 Carrier attribution

Every frame record, every bearing and every health event carries its carrier
(FR-6.6).

### 20.4 Retrieval

Logs are local files, rotated by size. They are retrieved **over WLAN** when
co-located, or read on the node.

---

## 21. Error Handling & Safe States

### 21.1 Principle

**Indicate, do not conceal.** A fault that cannot be resolved automatically is
surfaced and left for a human (N4).

### 21.2 Requirements

- **FR-21.1** [Must] No fault shall be silently retried indefinitely. Retries shall
  be bounded, counted, and reported.
- **FR-21.2** [Must] Exactly one class of automatic remediation exists: the single
  full-set configuration push on CRC mismatch (FR-7.7). Everything else is
  indicated.
- **FR-21.3** [Must] Absence of data shall be represented explicitly. A stale value
  shall never be presented as current (§9.7, §14.5).
- **FR-21.4** [Must] A node shall degrade rather than exit: loss of the Kraken feed,
  a carrier, or the DTU shall reduce function, not terminate the service.
- **FR-21.5** [Must] Counters — discards, CRC failures, resyncs, restarts,
  unconfigured-station frames, dropped log records — shall be exposed in the UI, not
  only in the log.

### 21.3 Safe states

| Subsystem | Safe state |
|-----------|-----------|
| Station, no Kraken feed | Answers polls, reports `no_data` |
| Station, no carrier | Continues measuring; the Management Pi shows `LOST` |
| Management Pi, station silent | Retains last values, greyed with age; keeps polling |
| Configuration diverged | Latched after one resync attempt; no further automatic change |
| DTU unconfigurable | Assume pre-configured, warn, continue |

---

## 22. Security

### 22.1 Scope

The threat model for v1 is **accidental**, not adversarial: the system shall not
silently accept malformed, duplicated, stale, or wrongly-addressed data. Protection
against a deliberate attacker on the RF medium is out of scope for v1.

### 22.2 What is and is not a control

- **Integrity within the system**: framing CRC (§10.2), sequence numbers and
  duplicate suppression (FR-10.7), address filtering (FR-18.3), and the
  configuration CRC (§7.4).
- **`AT+KEY` is not a security control.** The device offers AES keyed by a **16-bit**
  value, and the key is write-only, so it cannot be audited. It may be set as a
  network separator. It shall not be described as providing confidentiality (A6).
- **No confidentiality of bearings** is provided or claimed.

### 22.3 Requirements

- **NFR-22.1** [Must] Malformed or wrongly-addressed frames shall be discarded and
  counted, never partially applied.
- **NFR-22.2** [Must] A replayed frame shall not be applied twice (FR-10.7).
- **NFR-22.3** [Must] The management UI shall bind to an operator-configured
  interface, defaulting to the local network, not to a public interface.
- **NFR-22.4** [Must] Secrets present in the KrakenSDR settings — notably
  `krakenpro_key` — shall not be written to the debugging log.
- **NFR-22.5** [May] App-layer authentication of configuration frames is deferred;
  if added, a shared-secret truncated MAC over the frame is the intended mechanism
  (§3.3).

---

# Part E — Operations & Verification

## 23. Operational Procedures

### 23.1 Deploy

1. Provision each Pi by sparse-checkout of its own target — see
   [deployment.md](deployment.md).
2. Place configuration in `/etc/hornethunter/` (§19.2, §17.2).
3. Install and enable the systemd unit (§17.2).

### 23.2 Commission a station

1. Operator performs all RF setup — frequency, array geometry, spacing, and
   **manual alignment of the array to 0° heading** (§1.2, A3). A misalignment
   becomes a silent bearing error (§9.4); verify it by independent means.
2. Enable remote configuration: set `en_remote_control` true in the KrakenSDR
   settings locally, so a write route is available (§13.1), and set
   `doa_data_format = Kraken Pro Local` so the DoA feed is served (§12.2).
3. Start the node; the agent provisions its DTU (§11) and connects to the Kraken
   feed (§12).
4. From the Management Pi, perform a manual **full-set read** (FR-7.8) — a station
   with no mirror entry has no baseline for deltas (§7.7).
5. Confirm the station reports `GREEN` on the intended carrier (§8.3) and that
   bearings arrive with plausible age.

### 23.3 Operate

Watch the numeric rows and the two independent indicators — link health (§8.3) and
configuration state (§7.5), each labelled with its carrier (§8.5). Carrier selection
is automatic (§6); pin it (FR-6.5) when characterising one carrier.

### 23.4 Reconfigure

Edit a field in the UI (§14.3); a delta is sent (§7), applied live (§13.3), and
confirmed by read-back CRC (§7.5). Use *revert to last known good* (FR-14.7) if a
change misbehaves.

### 23.5 Recover

| Symptom | Path |
|---------|------|
| Station `RED` or `LOST` | §8.3, §6.5 — carrier state, then power and antenna |
| `CONFIG_DIVERGED` latched | §7.7 — inspect read-back differences in the log, then manual full push |
| No bearings, link healthy | §12.5 — Kraken feed, then `doa_data_format` |
| Station read-only, pushes rejected | §13.1 — enable `en_remote_control` locally |
| Bearings implausible but link and config healthy | Operator-owned RF domain (§1.2): alignment, array, squelch |
| DTU passes no data | §15.5 — possibly stuck in AT mode; `AT+REBOOT` or power cycle |

---

## 24. Verification & Validation

### 24.1 Test architecture

Three tiers, cost-ordered. Each behaviour is tested at the **lowest tier where its
bug can manifest**.

| Tier | Environment | Speed | Covers |
|------|-------------|-------|--------|
| **host** | pure Python, no hardware, no network | ms | frame codec, CRC, RSSI-byte stripping, fragmentation, ARQ state machine under injected loss, delta computation, canonical config encoding, health state machine, bearing encode/decode, adapter field mapping |
| **bench** | Universal Embedded Workbench, two real DTUs on RFC2217, plus `simulator` or `synthetic` DoA source | s–min | DTU provisioning over AT, real transparent-mode framing including split and coalesced packets, poll cycle timing and slotting, carrier switching, end-to-end parameter push with read-back, sustained-run stability |
| **field** | real antennas, real separation, real KrakenSDR | hours | link reliability at range, health threshold calibration, everything whose behaviour depends on link margin |

**Layer-to-tier mapping.** L2 application logic is pure and tested at the host tier.
L1 interfaces are split — pure core at the host tier, wire and flow behaviour at the
bench tier. L0 foundation has **no tests of its own** and is exercised transitively
through the L1 chapters that use it (§15.5, §16.4).

Measured airtime on the bench is identical with dummy loads and with antennas
(Appendix B); reliability is not. Every reliability threshold in §8.4 is provisional
until calibrated at the field tier.

### 24.2 Acceptance tests

| ID | Objective | Tier | Requirements |
|----|-----------|------|--------------|
| AT-1 | Codec round-trips every message type, including maximum payload and fragmented sets | host | FR-10.1, FR-10.3, FR-10.6 |
| AT-2 | ARQ delivers all frames at 0 %, 10 % and 50 % injected loss; reports exhaustion at 100 % | host | FR-10.7, §10.5, FR-21.1 |
| AT-3 | Split, coalesced and garbage-prefixed byte streams are recovered without loss of valid frames | host | FR-10.1, §10.6 |
| AT-4 | RSSI-append byte is stripped before CRC validation; frames validate with append on and off | host | FR-10.4 |
| AT-5 | Canonical CRC is stable across JSON reformatting and float re-serialisation, and unaffected by GPS-mutated fields | host | FR-7.4 |
| AT-6 | Health state machine produces the specified colour for constructed retry sequences; window resets on carrier change | host | FR-8.1–FR-8.5 |
| AT-7 | Simulator and real record shapes both map to the internal type | host | FR-12.3, NFR-12.1 |
| AT-8 | DTU provisioning is idempotent; AT mode is always exited, including on injected error | bench | FR-11.2, FR-11.5, NFR-11.1 |
| AT-9 | Two stations poll continuously for one hour with no unexplained gaps; all cycles accounted for in the log | bench | FR-5.1–FR-5.4, NFR-20.1 |
| AT-10 | Single-field change reaches a station, applies live, and is confirmed by read-back CRC | bench | FR-7.1, FR-13.1, FR-13.2 |
| AT-11 | Externally corrupted station configuration raises `CONFIG_DIVERGED`, is repaired by exactly one automatic full push, and latches if corrupted again | bench | FR-7.7 |
| AT-12 | Carrier switch during traffic loses no frame; window resets; carrier stamped on every record | bench | NFR-6.1, FR-6.6, FR-8.5 |
| AT-13 | Station remains pollable with the KrakenSDR software stopped, and reports `no_data` | bench | FR-13.4, NFR-16.1, FR-9.6 |
| AT-14 | Management restart resumes delta operation from the persisted mirror with no full push | bench | FR-7.9 |
| AT-15 | Link stays out of `ORANGE` for a sustained run at intended deployment range | field | §8.3 |
| AT-16 | Health thresholds calibrated against measured retry behaviour at range | field | §8.4, NFR-8.2 |

### 24.3 Traceability

Traceability is **generated**, never hand-maintained here. Each requirement carries
a stable ID in its owning chapter, and each test spec cites the IDs it exercises;
the traceability tool crosses them to produce:

- `tests/coverage-matrix.md` — component × tier coverage
- `tests/gaps.md` — Must/Should requirements with no referencing test

Test specs mirror the chapter spine one-to-one.

---

## Appendices

### Appendix A — Carrier constants

| Constant | Value | Note |
|----------|-------|------|
| Max single DTU packet | 240 B | larger writes auto-packetise |
| HH-Link frame cap | 209 B | 9 B header + 200 B payload; stays in one packet |
| Internal DTU cache | 960 B | fixed |
| PHY CRC | always on | cannot be disabled (§15.3) |
| UART | 115200 8N1 | open with DTR and RTS deasserted |
| Sync word | `0xA5 0x5A` | §10.2 |
| Frame CRC | CRC-16/CCITT-FALSE | over VER..PAYLOAD |

### Appendix B — Measured airtime

SF7 / BW 125 kHz / CR 4-5, byte-exact at every size. Measured over the workbench on
two DTUs; identical with antennas and with dummy loads.

| payload | latency | throughput |
|--------:|--------:|-----------:|
| 4 B | 61 ms | 66 B/s |
| 16 B | 81 ms | 198 B/s |
| 32 B | 102 ms | 314 B/s |
| 64 B | 162 ms | 396 B/s |
| 128 B | 263 ms | 487 B/s |
| 200 B | 385 ms | 520 B/s |
| 240 B | 444 ms | 540 B/s |

A fixed cost of ~60 ms per packet dominates small frames, so a 10-byte bearing
record and a 20-byte delta cost almost the same airtime.

Derived frame sizes: `POLL` ≈ 12 B, `BEARING` ≈ 19 B (23 B with position),
single-field `PARAM_DELTA` ≈ 12 B. A full set over 158 fields (§7.3), serialising
only VFO slots up to `active_vfos` (FR-7.10), runs to several hundred bytes and
**3–4 fragments, on the order of 1–1.5 s**.

### Appendix C — Defaults

| Key | Default |
|-----|---------|
| `cycle.period_ms` | 1000 |
| `cycle.guard_ms` | 40 |
| `cycle.slot_ms` | 150 |
| `arq.timeout_ms` | 400 |
| `arq.max_attempts` | 3 |
| `health.window_cycles` | 20 |
| `health.retry_rate_threshold` | 0.20 |
| `health.stale_cycles` | 5 |
| `carrier.probe_interval_s` | 5 |
| `carrier.probe_timeout_s` | 1 |
| `carrier.promote_probes` | 3 |
| `carrier.demote_probes` | 2 |
| `carrier.dwell_s` | 30 |
| `bearing.position_epsilon_dm` | 50 |
| `bearing.max_age_ms` | 5000 |
| `kraken.param_apply_timeout_s` | 5 |
| `link.lbt` | 0 |

Radio parameters (`link.channel`, `link.sf`, `link.bw`, `link.cr`, `link.power`)
have **no defaults asserted here** — they are operator-owned (§1.2, FR-19.3).

---

## Related

- [[lora-dtu-sx1262]] — DTU behaviour and AT reference
- [[krakensdr-integration]] — KrakenSDR API contract
- [[krakensdr-wiki/README]] — vendored upstream KrakenSDR documentation
- [[deployment]] — two-target sparse-checkout deployment
