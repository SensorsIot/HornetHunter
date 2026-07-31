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

- The station↔management data link: framing, the streamed bearing path and the
  acknowledged configuration path (ARQ), addressing, and link health indication.
- The carrier model: LoRa as the sole operational link for that data path, and WLAN
  as an out-of-band setup and management path (§7).
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
- G4 — Operate over the LoRa link alone during a mission; WLAN is a pre-mission
  setup and management convenience, never required in the field.

**Non-goals**

- N1 — No position fix in v1.
- N2 — No graphical display: numbers only.
- N3 — No cloud service, no internet dependency of any kind at runtime.
- N4 — No **unbounded or silent** remediation. Field faults self-heal through
  bounded, logged, escalating recovery (§5, FR-22.2); a fault
  that survives its recovery budget is indicated and left for a human, never
  silently retried.

### 1.5 High-level flow

```
   KrakenSDR ──► krakensdr_doa ──CSV/HTTP──► KrakenProxy   ──┐
   (station 1)   (:8081 DOA_value.html)                      │
                                                     HH-Link  │  bearings
   KrakenSDR ──► krakensdr_doa ──CSV/HTTP──► KrakenProxy   ──┤  (LoRa)
   (station 2)   (:8081 DOA_value.html)                      │
                                                              ▼
                                              ┌──────────────────────────┐
                                              │  Management Pi           │
                                              │  receive · health · UI   │
                                              │  numeric display + log   │
                                              └──────────────────────────┘
                        configuration deltas ──────────┘ (same link, reverse)
```

---

## 2. System Architecture

### 2.1 Logical architecture

The two directions of the link have different reliability needs, so they use
different mechanisms.

**Bearings stream, unacknowledged.** Each station transmits a **BEARING**
autonomously whenever its KrakenSDR produces a new direction estimate (~2.3 Hz,
§13) — no poll, no schedule, no acknowledgement. A lost bearing is simply skipped:
the next estimate is ~0.4 s behind, so retransmitting a stale angle is pointless.
The Management Pi is a passive receiver for bearings; a station's liveness is
judged purely by whether fresh bearings keep arriving (§9).

**Configuration is request/response, acknowledged.** The Management Pi is the only
node that initiates traffic: it reads and writes KrakenSDR configuration, each
exchange explicitly acknowledged and retransmitted until confirmed (§11.5). This is
occasional and operator-driven, and is the *only* path that uses ARQ.

Stations share the medium best-effort (§19.2). At the bearing duty cycle of a few
stations, collisions are rare and a collided bearing is just another skipped one.

### 2.2 Hardware / platform architecture

| Node | Hardware | Attached |
|------|----------|----------|
| Management Pi | Raspberry Pi (`HornetManager`) | LoRa DTU (USB), `wlan0` access point (field subnet), `eth0` development uplink / NAT gateway |
| Kraken Pi × 2 | Raspberry Pi | KrakenSDR (USB), LoRa DTU (USB), u-blox GNSS (USB), `wlan0` (field client), `eth0` (emergency recovery) |

The LoRa DTUs are USB dongles presenting a serial port; on the development bench
they are reached over the Universal Embedded Workbench as RFC2217 network serial
ports, and on a Pi as a local device node. The software treats both identically
(§16.2).

**Network topology.** The HornetHunter network is self-contained and isolated from
any home or site network. Two independent links connect the Management Pi and the
stations, with distinct roles (§7):

- **LoRa — the operational link, always.** All HH-Link traffic — the streamed
  bearings and the acknowledged configuration exchange — rides the LoRa DTUs between
  each station and the Management Pi, whether or not WLAN is present.
- **WLAN — out-of-band setup and management, when in range.** The Management Pi runs
  a `wlan0` access point on the `192.168.50.0/24` field subnet (address
  `192.168.50.1`); every Kraken Pi associates to it as a WLAN client and receives a
  fixed `192.168.50.x` lease. This field subnet carries **no** HH-Link protocol: it
  is the SSH path over which the Pi, the KrakenSDR software and the network are set
  up and managed — typically before the mission — and over which logs are retrieved
  (§21.3). Stations may be out of WLAN range in the field; the mission does not
  depend on it.

The Management Pi's `eth0` is the **sole** connection to an outside (home/development)
network and exists only for development access; the
Management Pi NATs the field subnet out through it, so stations reach the outside
solely through the Management Pi and are never exposed on, or reachable from, the
outside network. Each Kraken Pi also keeps a wired `eth0`, held in reserve for
emergency recovery only. The reproducible configuration is §18.4.

**DTU device node.** The LoRa DTU is addressed by its `/dev/serial/by-id/` path
(`usb-1a86_USB_Single_Serial_<serial>-if00`), not a `ttyACM` index. The u-blox GNSS
receiver shares the `ttyACM` namespace and enumeration order is not stable across
boots (NFR-16.3).

Station count is a configuration property. The addressing scheme (§19) and the
best-effort bearing stream (§6) accommodate additional stations without protocol
change.

### 2.3 Software architecture

Three Python packages in this repository:

| Package | Runs on | Role |
|---------|---------|------|
| `hornethunter_shared` | both | wire contract, field registry, framing, geometry |
| `hornethunter_kraken` | Kraken Pi | KrakenProxy |
| `hornethunter_management` | Management Pi | master, UI, log aggregation |

Each Pi runs one long-lived systemd service. Configuration lives outside the
repository in `/etc/hornethunter/`.

**Persistence.** The Management Pi persists a per-station configuration mirror and
the previous known-good snapshot to disk (§8.6). Both nodes append structured logs
to local rotating files (§21). Nothing else is persisted in v1.

**A KrakenProxy shall not depend on the health of its KrakenSDR software.** It is
a separate process; it continues to stream bearings and accept configuration
pushes when the DSP is stopped, wedged, or misconfigured (§14.4).

### 2.4 Component layering

Strict one-way dependency: **L0 Foundation → L1 Interfaces → L2 Application
logic**. The L0/L1 line is ownership — whether the protocol is implemented here.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ L2  Application logic                                                        │
│  ┌────────────┐ ┌───────────┐ ┌──────────────┐ ┌───────────┐ ┌────────────┐  │
│  │  Bearing   │ │ Carrier   │ │  Parameter   │ │  Link     │ │  Bearing   │  │
│  │  Stream    │ │ Model     │ │ Distribution │ │  Health   │ │  Pipeline  │  │
│  │   §6       │ │    §7     │ │     §8       │ │   §9      │ │    §10     │  │
│  └────────────┘ └───────────┘ └──────────────┘ └───────────┘ └────────────┘  │
├──────────────────────────────────────────────────────────────────────────────┤
│ L1  Interfaces (wire logic implemented here)                                 │
│  ┌────────────┐ ┌───────────┐ ┌──────────────┐ ┌───────────┐ ┌────────────┐  │
│  │  HH-Link   │ │ DTU AT    │ │ Kraken DoA   │ │  Kraken   │ │ Management │  │
│  │ Frame+ARQ  │ │ Provision │ │   Source     │ │  Settings │ │  UI        │  │
│  │   §11      │ │   §12     │ │    §13       │ │    §14    │ │   §15      │  │
│  └────────────┘ └───────────┘ └──────────────┘ └───────────┘ └────────────┘  │
├──────────────────────────────────────────────────────────────────────────────┤
│ L0  Foundation (configured & used, not implemented here)                     │
│  ┌───────────────────────┐ ┌────────────────────────┐ ┌───────────────────┐  │
│  │ LoRa DTU & byte       │ │ krakensdr_doa +        │ │ Host platform     │  │
│  │ carriers   §16        │ │ middleware      §17    │ │        §18        │  │
│  └───────────────────────┘ └────────────────────────┘ └───────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Source layout mirrors the layers.** Each component is its own module; an
interface is never folded into its consumer. Lower layers never import higher ones;
where a foundation component notifies upward, it does so through a callback
registered at the composition root (the service entry point). Each L1 interface's
pure core — frame encode/decode, CRC, delta computation, canonical encoding,
state-machine transitions — is a set of free functions taking plain data, testable
at the fast tier with no hardware and no I/O (§25.1).

---

## 3. Implementation Phases

### 3.1 Phase 1 — Link foundation

**Scope.** The wire contract and its reliability, without any radio.

**Deliverables**

- `hornethunter_shared`: frame codec (§11.2), CRC, field registry (§8.2),
  canonical config encoding (§8.4), bearing record (§10.3).
- ARQ state machine (§11.5) and the byte-carrier abstraction (§16.2).
- Loopback and in-process link simulator; host-tier test suite.

**Exit criteria.** Frame codec round-trips all message types including
fragmentation; ARQ recovers from injected loss at 0/10/50 % rates; the entire suite
runs with no hardware attached.

### 3.2 Phase 2 — Real link and real Kraken

**Scope.** Phase 1 over the two physical DTUs and against the KrakenSDR software.

**Deliverables**

- DTU provisioning over AT (§12), bearing stream (§6), health evaluator (§9).
- Kraken DoA source with its three backends (§13.3) and settings client (§14).
- Parameter distribution end to end with divergence detection (§8).
- Management UI: numeric display, all parameter panels, log pane (§15).
- Structured logging on both nodes (§21).

**Exit criteria.** Both stations stream bearings continuously over LoRa on the bench for one
hour with health reported and no unexplained gaps; a parameter change reaches a
station and is confirmed by read-back; a corrupted station configuration raises
`CONFIG_DIVERGED` and is repaired by one automatic full push.

### 3.3 Phase 3+ — Deferred

| Item | Note |
|------|------|
| Position fix (triangulation) | `geo.triangulate()` exists and is tested; unused in v1 (§10.6) |
| Graphical / map display | v1 is numeric only (N2) |
| Live GPS-driven heading | heading is a constant in v1 (§10.4) |
| Cross-station time alignment | not required until fixes exist (§10.5) |
| App-layer authentication | §23.3 |

---

## 4. Assumptions & Dependencies

### 4.1 Assumptions

- A1 — Two stations in v1, extensible by configuration.
- A2 — The operator sets all radio and array parameters manually; the software does
  not validate them for physical plausibility (§1.2).
- A3 — Station array **heading is fixed at 0°** by manual alignment and is not
  transmitted (§10.4).
- A4 — Station position is supplied by the KrakenSDR feed, may change, and is
  transmitted only when it changes (§10.4).
- A5 — No internet at runtime. All UI assets are served locally (§15.2).
- A6 — `AT+KEY`, if used, is a network separator and **not** a security control
  (§23.2).

### 4.2 Dependencies

| Dependency | Used for | Notes |
|------------|----------|-------|
| `krakensdr_doa` + its Node middleware | bearings and settings | §13, §14, §17 |
| SX1262 LoRa DTU (transparent mode) | LoRa carrier | see [lora-dtu-sx1262.md](lora-dtu-sx1262.md) |
| `pyserial` | serial and RFC2217 carriers | RFC2217 used on the bench |
| Universal Embedded Workbench | bench tier | two DTUs on RFC2217 ports |

---

## 5. KrakenProxy

### 5.1 Purpose and peer

The **KrakenProxy** is the station process — the bridge between the KrakenSDR
software and the USB-LoRa link. It runs on each Kraken Pi, **co-located** with the
KrakenSDR software: it reads that software over **localhost** HTTP and drives the
LoRa DTU over **local USB**, both on the one host. Its peers are the KrakenSDR
software (north) and the Management Pi (south, over LoRa). It is the composition root
that wires the L1 interfaces (§11–§15) and the L0 foundation (§16–§18) into a running
station; the per-concern behaviour it composes is specified in those chapters, and
its own job is to **bridge** them, **supervise** them, and keep the station streaming
unattended.

### 5.2 Interfaces

Two interface families meet in the KrakenProxy.

**North — the KrakenSDR software (localhost):**

| Interface | Path | Purpose | Spec |
|-----------|------|---------|------|
| DoA read | `GET :8081/DOA_value.html` | bearings (CSV, ~2.3 Hz) | §13 |
| Settings read-back | `GET :8081/settings.json` | CRC of what the DSP holds | §14 |
| Settings write | atomic `_share/settings.json` + `ext_upd_flag` | apply config | §14 |

**South — the USB-LoRa DTU (local serial):**

| Interface | Detail | Spec |
|-----------|--------|------|
| Byte carrier | `/dev/serial/by-id/…` DTU, 115200 8N1, transparent mode | §16 |
| Provisioning | AT-mode setup at startup (address, channel, SF/BW/CR) | §12 |
| HH-Link frames | BEARING out (in slot), BEACON/JOIN, config ACK/PARAM_* | §11 |

Nothing crosses unchanged: the KrakenProxy transforms between the KrakenSDR's
HTTP/CSV/JSON world and the HH-Link frame world.

### 5.3 The two data paths

- **Bearing path (KrakenSDR → LoRa):** poll the DoA CSV (§13), map it to the internal
  measurement (§13), run the bearing pipeline (§10), and transmit a BEARING in the
  station's TDMA slot (§6). Rate-limited to the slot rate; the newest estimate wins.
- **Config path (LoRa → KrakenSDR):** on a `PARAM_DELTA` (§8, §11), merge and write
  the settings file atomically with `ext_upd_flag` (§14), re-read to compute the
  canonical CRC (§8), and ACK with the observed config version and CRC. A
  retransmitted delta is applied once (§11).

### 5.4 TDMA participation

The KrakenProxy is a **scheduled** participant, not a free-running transmitter (§6).
It synchronises to the master's beacon, transmits its bearing only in its granted
slot, `JOIN`s via the contention window on start or relink, and — on beacon loss —
goes silent, keeps measuring, holds its latest bearing, and rejoins when the beacon
returns (FR-6.7). It never transmits without a valid beacon.

### 5.5 Supervision, watchdog & autorecovery

The station shall run **fully automatically and unattended** (NFR-18.4): it boots,
provisions its DTU (§12), connects to the KrakenSDR feed (§13), joins the schedule
(§6), and streams — with no operator action. Beyond keeping itself alive, the
KrakenProxy **supervises the KrakenSDR software** and self-heals in the field:

- **Watchdogs.** It watches (a) the DoA feed (bearings advancing), (b) the KrakenSDR
  DSP/services being up, and (c) the beacon. Its own liveness is guarded by its
  systemd service (§18, `Restart=on-failure`).
- **Bounded, escalating autorecovery (N4, FR-22.2).** On a stalled feed or wedged DSP
  it recovers automatically — re-open the feed, restart the `krakensdr` services,
  re-provision the DTU, rejoin the schedule — each attempt **counted, logged, and
  backed off**, up to a bounded budget.
- **Indicate what survives.** A fault that outlasts the recovery budget is left
  **indicated** — the station goes stale and the Management Pi shows it RED (§9) — not
  silently retried.

The one capability beyond the localhost data interfaces this needs is **lifecycle
control of the KrakenSDR software** (a service restart) plus the provisioning-time
settings write; supervision reaches for those, while the steady-state data path stays
on HTTP/CSV and the settings file.

### 5.6 Requirements

- **FR-5.1** [Must] The KrakenProxy shall run on the same host as the KrakenSDR
  software, reading it over localhost and driving the local USB-LoRa DTU.
- **FR-5.2** [Must] It shall bridge the two interface families (§5.2): DoA CSV in →
  BEARING out; `PARAM_*` in → settings-file write + read-back CRC → ACK.
- **FR-5.3** [Must] It shall participate in the master schedule (§6): beacon sync,
  slot transmit, `JOIN`, and go-silent-and-rejoin on beacon loss.
- **FR-5.4** [Must] It shall reach a streaming state **unattended** after a power
  cycle, with no operator action (NFR-18.4).
- **FR-5.5** [Must] It shall supervise the KrakenSDR software — watchdogs on the feed,
  the DSP, and the beacon — and attempt **bounded, logged, escalating** autorecovery
  (FR-22.2); a fault that survives its budget shall be indicated (§9), not silently
  retried.
- **FR-5.6** [Must] It shall never gate its own liveness on the health of the
  KrakenSDR software (§17): it keeps streaming and accepting config while recovering
  the DSP.
- **NFR-5.1** [Must] The bridge transforms (CSV↔record, delta↔settings file) shall be
  pure and unit-testable without hardware (§25.1).

### 5.7 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| DoA feed stalled | Watchdog fires; bounded recovery (reopen feed, restart DSP); heartbeats continue; RED if unrecovered (§9) |
| KrakenSDR DSP wedged/stopped | Recovery restarts the services; the KrakenProxy keeps running (§17) |
| DTU absent / serial lost | Reprovision/reopen with backoff; no operational link → master shows RED (§9, §22) |
| Beacon lost | Go silent, keep measuring, rejoin on return (§6, FR-6.7) |
| Settings file unwritable | Report config-unwritable (§14, FR-14.6); reads still served |
| Fault survives recovery budget | Indicated: station RED at the Management Pi (§9, N4) |

---

# Part A — Application Logic (L2)

## 6. Bearing Stream & TDMA Schedule

### 6.1 Purpose and scope

Governs how bearings move from stations to the Management Pi and how the shared LoRa
channel is scheduled. Bearings are **streamed** — each KrakenProxy sends its own,
unacknowledged (§2.1) — but the channel is **master-scheduled TDMA**: the Management
Pi is the sole timing authority, and each station transmits only in the slot(s) the
master grants. This keeps the channel collision-free even though stations cannot be
assumed to hear one another (hidden-node topology, §7).

### 6.2 The superframe

Time is a repeating **superframe** the master defines with a periodic **beacon**:

- **Beacon** (broadcast, master → all): carries the superframe timing-zero and the
  **live slot-map** — which live station number owns which slot. Absent or not-live
  numbers reserve nothing; the map compacts to the live set.
- **Slots** are ~125 ms (a bearing frame ~90 ms airtime + guard). At the operational
  SF7 / BW125 / CR4-5 (Appendix A) the channel yields ~8 slots/s, so a 1 s superframe
  is: beacon + up to three station data slots ×2 (**2 Hz per station** at ≤3
  stations) + a join/contention window.
- A station transmits its latest bearing **only** in its mapped slot(s), timed from
  the beacon. There is **no LBT** — carrier-sense is defeated by hidden nodes and
  made unnecessary by exclusive slots.
- **Adaptive rate:** with fewer live stations the freed slots let each survivor hold
  more slots per superframe, up to the DoA native ~2.3 Hz; the per-station rate
  downsamples only as stations join and the frame fills.

### 6.3 Join and departure

A station with no slot (boot, or after a link loss) obtains superframe timing from
the beacon, then transmits a **JOIN** in the beacon's contention window; the master
adds it to the next beacon's slot-map, after which it streams in its slot. Random
backoff resolves the rare simultaneous JOIN. The master drops a station from the
map when its bearings go stale past the threshold (§9); the slot frees and the map
compacts. Configuration for a departed-then-returning station is reconciled on
rejoin (§8.6, §15).

### 6.4 Requirements

- **FR-6.1** [Must] The Management Pi shall be the sole timing authority, broadcasting
  a beacon each superframe carrying the timing-zero and the live slot-map.
- **FR-6.2** [Must] A station shall transmit a BEARING **only** in a slot the current
  beacon assigns it, timed from that beacon, and shall not transmit without a valid
  beacon.
- **FR-6.3** [Must] Bearings shall be unacknowledged and never retransmitted; a lost
  bearing is skipped (§2.1).
- **FR-6.4** [Should] A station shall send its most recent estimate in its slot,
  collapsing to the newest when the DoA cadence exceeds its slot rate (Appendix B).
- **FR-6.5** [Must] A station without a slot shall obtain timing from the beacon and
  request one via a JOIN in the contention window, backing off on collision.
- **FR-6.6** [Must] The master shall derive slots from station numbers (§19),
  compacting so not-live numbers reserve no slot, and shall adapt each station's slot
  count to the live-station load.
- **FR-6.7** [Must] On loss of the beacon a station shall cease transmitting, keep
  measuring and hold its latest bearing, and rejoin when the beacon returns (the
  KrakenProxy chapter).
- **FR-6.8** [Must] The Management Pi shall accept a BEARING from any configured
  station, record its arrival time, and forward it to health (§9) and the UI (§15).
- **FR-6.9** [Must] A BEARING from an unconfigured station id shall be discarded,
  counted, and logged at most once per minute (§19).
- **NFR-6.1** [Must] Bearing ingest shall be independent of configuration traffic: an
  in-flight configuration exchange shall not block or delay bearing reception.
- **NFR-6.2** [Must] Superframe period, slot length, guard and slot count shall be
  configuration with documented defaults (Appendix C).

### 6.5 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Beacon lost (master down / link lost) | Station stops transmitting, keeps measuring, holds latest bearing, rejoins on beacon return (FR-6.7); master shows it RED (§9) |
| No bearing past the staleness threshold | Station declared stale (RED) by §9; nothing is retransmitted |
| Bearing lost to a CRC error | Discarded silently; the next slot supersedes it |
| Two stations JOIN in the same window | Both back off randomly and retry (FR-6.5) |
| DoA cadence exceeds the slot rate | Newest estimate sent, intermediate ones dropped (FR-6.4) |
| Bearing from an unconfigured station | Discarded, counted, logged once per minute |

---

## 7. Carrier Model

### 7.1 Purpose and scope

Defines which network carries what. The model is **fixed**, not selected — there is
no runtime carrier switching.

- **LoRa is the sole operational carrier.** Every HH-Link message — streamed
  bearings (§6) and the acknowledged configuration exchange (§8, §11.5) — travels
  between each station and the Management Pi over LoRa, always, whether or not WLAN
  is present.
- **WLAN is out-of-band setup and management.** The field-subnet access point
  (§18.4) is an SSH path for provisioning and managing the Pi, the KrakenSDR
  software and the network — typically before the mission — and for retrieving logs
  when co-located (§21.3). It carries no HH-Link protocol, and the mission does not
  depend on it.
- **Channel access is master-scheduled.** Only master↔station links are assumed
  reliable — stations may not hear one another (hidden-node topology) — so the
  Management Pi schedules all channel access as TDMA (§6). Carrier-sense/LBT is not
  used: it is defeated by hidden nodes and made unnecessary by exclusive slots.

### 7.2 Requirements

- **FR-7.1** [Must] All HH-Link traffic — bearings and the configuration exchange —
  shall ride the LoRa carrier. The operational station↔management link shall not
  depend on WLAN.
- **FR-7.2** [Must] WLAN shall be used only for out-of-band setup, management and
  log retrieval (§18.4, §21.3); no HH-Link frame shall be sent over it.
- **FR-7.3** [Must] A mission shall proceed with WLAN absent. Loss or absence of
  WLAN shall not affect bearings, the configuration exchange, or link health (§9).
- **FR-7.4** [Must] Only master↔station links shall be assumed reliable; the design
  shall not require a station to hear another station. The Management Pi shall
  schedule all channel access (§6), and a station shall transmit only in a
  master-granted slot, without carrier-sense/LBT.
- **NFR-7.1** [Must] A LoRa link interruption shall not lose an in-flight
  configuration transaction; the pending ARQ transaction (§11.5) resumes when the
  link returns.

### 7.3 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| LoRa link down | No bearings arrive; the station goes `RED` (§9). The agent keeps measuring (§22.3) |
| WLAN absent | No effect on operation; setup and log retrieval wait until the station is back in range |
| LoRa interrupted mid-config | The in-flight ARQ transaction stays pending, not lost, and resumes on return (NFR-7.1) |

---

## 8. Parameter Distribution & Configuration Integrity

### 8.1 Purpose and scope

Delivers the KrakenSDR configuration set from the Management Pi to the stations, and
continuously verifies that a station holds what the Management Pi holds for it. This
chapter owns the delta mechanism, the canonical CRC, and the divergence response.

### 8.2 Requirements

- **FR-8.1** [Must] Configuration changes shall be transmitted as **deltas** — only
  the fields that changed.
- **FR-8.2** [Must] Every field shall have a stable numeric identifier and a
  declared type, held in a shared **field registry** in `hornethunter_shared`.
- **FR-8.3** [Must] The Management Pi shall maintain a `config_version` per station,
  incremented on every accepted change.
- **FR-8.4** [Must] A **canonical CRC** over the operator-owned configuration shall
  be computed identically on both nodes (§8.4).
- **FR-8.5** [Must] Each station shall compute its CRC from the configuration **read
  back from the KrakenSDR software**, not from the delta it was asked to apply
  (§8.5).
- **FR-8.6** [Must] Every BEARING record shall carry the station's `config_version`
  and CRC.
- **FR-8.7** [Must] On a canonical-CRC mismatch the Management Pi shall raise
  `CONFIG_DIVERGED`, perform **exactly one** automatic full-set push, and set a
  sticky "resynced" marker on that station. If the mismatch persists after that
  push, it shall stop and require operator action. The CRC is the authoritative
  end-to-end check; `config_version` is the Management Pi's own mirror counter and
  the station's held version, not a value echoed across the link.
- **FR-8.8** [Must] The operator shall be able to request a full-set read from a
  station, and a full-set push to a station, as explicit manual operations.
- **FR-8.9** [Must] The Management Pi shall persist its per-station configuration
  mirror across restarts.
- **FR-8.10** [Should] A full-set encoding shall serialise only VFO slots up to
  `active_vfos`.
- **NFR-8.1** [Must] A single-field delta shall fit in one frame.

### 8.3 Field registry

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
| `crc_covered` | whether it participates in the canonical CRC (§8.4) |
| `restart_hint` | informational only; the KrakenSDR applies changes live (§14.3) |

A delta entry on the wire costs `1 B id + 1..4 B value` for scalars; a typical
three-field change is ~20 bytes — one frame (NFR-8.1).

### 8.4 Canonical CRC

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

### 8.5 Divergence detection

```
management                                     station
   │ delta(fields, version=N, crc=C) ─────────────►│
   │                                               │ apply via §14
   │                                               │ read back via §14
   │                                               │ crc' = canonical(read-back)
   │◄──────────── ACK(version=N, crc=crc') ────────│
   │ crc' == C ?  yes → in sync
   │              no  → CONFIG_DIVERGED
   │
   │◄──── BEARING(version, crc) ───── streamed ──│   continuous re-verification
```

The CRC is computed from the read-back, so the check catches the KrakenSDR clamping
a value, rejecting it, or its settings watcher never observing the write. The CRC
rides on every bearing, so a station whose configuration is changed locally is
detected within one bearing.

### 8.6 Revert and recovery

The Management Pi retains the previous accepted configuration snapshot per station.
With the KrakenProxy's independence from DSP health (§2.3), every exposed setting
is recoverable: a setting that breaks the KrakenSDR does not break the command
channel that undoes it.

### 8.7 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Delta ACKed, CRC mismatch | `CONFIG_DIVERGED`; one automatic full push; sticky marker (FR-8.7) |
| Mismatch persists after full push | Stop. `CONFIG_DIVERGED` latched; operator action required |
| Station unknown to the mirror (first contact) | No baseline for deltas; a manual full read is required (FR-8.8) |
| Delta not ACKed within ARQ budget | Change stays pending; version not advanced; §9 reports the link fault |
| Read-back fails (KrakenSDR unreachable) | ACK carries a `kraken_down` flag; CRC not asserted; distinguished from divergence |

---

## 9. Link Health Evaluator

### 9.1 Purpose and scope

Converts each station's **bearing arrival** into a single per-station liveness
indicator and into metrics for later audit. Health asks one question: *are fresh
bearings still arriving?* Because bearings stream unacknowledged (§6), there are no
retransmission counts to read — liveness is staleness. Signal strength (RSSI) is
displayed as information but does not contribute.

### 9.2 Requirements

- **NFR-9.1** [Must] Health shall be computed **exclusively** from bearing arrival.
  RSSI shall not contribute.
- **FR-9.1** [Must] The evaluator shall track, per station, the time since the last
  BEARING was received.
- **FR-9.2** [Must] The indicator shall be `GREEN` while a BEARING has arrived
  within the last `staleness_threshold_s`.
- **FR-9.3** [Must] The indicator shall become `RED` when no BEARING has arrived for
  longer than `staleness_threshold_s`, and shall return to `GREEN` on the next
  BEARING.
- **FR-9.4** [Should] The evaluator may report `ORANGE` when bearings are still
  arriving but the measured arrival rate over a rolling window falls below
  `orange_rate_fraction` of the expected rate — early warning short of going stale.
- **FR-9.5** [Must] `CONFIG_DIVERGED` (§8.5) shall be indicated **independently** of
  link health, not folded into it.
- **FR-9.6** [Must] The evaluator shall expose time-since-last-bearing, measured
  arrival rate and last-received RSSI for display and logging.
- **NFR-9.2** [Must] The staleness threshold and window shall be configurable at
  runtime.

### 9.3 States

| State | Condition | Meaning |
|-------|-----------|---------|
| 🟢 `GREEN` | a BEARING within `staleness_threshold_s` | receiving |
| 🟠 `ORANGE` | receiving, but arrival rate below `orange_rate_fraction` of expected (FR-9.4) | degraded, still receiving |
| 🔴 `RED` | no BEARING for longer than `staleness_threshold_s` | signal lost; operator action |
| 🟣 `CONFIG_DIVERGED` | CRC mismatch (§8.5) | independent axis; wrong-data fault, not a link fault |

Bearings arrive over the LoRa link only (§7); health is a verdict on that link.

### 9.4 Defaults

| Parameter | Default |
|-----------|---------|
| `staleness_threshold_s` | 1.0 |
| rate window | 10 s |
| `orange_rate_fraction` | 0.5 (below half the expected bearing rate) |

Threshold and window are runtime configuration (NFR-9.2).

### 9.5 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| No bearing ever received since start | `RED` once the staleness threshold elapses; no warm-up needed |
| Bearings arriving but sparse | `ORANGE` if below the rate fraction, else `GREEN`; nothing is retransmitted |

---

## 10. Bearing Pipeline

### 10.1 Purpose and scope

Runs on the station. Converts the KrakenSDR measurement stream into the compact
record streamed on each new estimate (§6).

### 10.2 Requirements

- **FR-10.1** [Must] The station shall subscribe to the DoA stream (§13) and retain
  the most recent measurement.
- **FR-10.2** [Must] The station shall transmit the **latest** measurement as each
  new estimate arrives, not an average or a batch; when the rate limit (FR-6.3)
  collapses estimates, it transmits the newest.
- **FR-10.3** [Must] Each record shall carry the measurement's **age in
  milliseconds** at the moment of transmission.
- **FR-10.4** [Must] The station shall report how many measurements were produced
  and discarded since the previous transmission.
- **FR-10.5** [Must] Station position shall be transmitted **only when it has
  changed** beyond `position_epsilon` since the last transmitted position.
- **FR-10.6** [Must] Records shall indicate whether the KrakenSDR feed is live, and
  shall be transmitted with a `no_data` indication when it is not.
- **NFR-10.1** [Must] A record without position shall not exceed 10 bytes of payload.

### 10.3 Record

| Field | Type | Bytes | Notes |
|-------|------|------:|-------|
| `flags` | `u8` | 1 | see below |
| `bearing_cdeg` | `u16` | 2 | centi-degrees, 0..35999 |
| `confidence` | `u8` | 1 | quantised from the feed's `conf` (§10.4) |
| `power_dbm` | `i8` | 1 | from the feed's `power` |
| `age_ms` | `u16` | 2 | measurement age at transmission (FR-10.3) |
| `config_version` | `u8` | 1 | §8.6 |
| `config_crc` | `u16` | 2 | §8.4 |
| `dlat`,`dlon` | `i16`×2 | +4 | decimetres from station reference; present only per FR-10.5 |

**10 bytes** normally, 14 with position. Flags: position-present, position-source,
kraken-link-up, squelch-open, adc-overdrive, no-data, reserved ×2.

The feed's `conf` is **not normalised to 0..1** (values above 100 are observed, e.g.
159); the station quantises it into `confidence` `u8` by clamping. `adc_overdrive`
and `squelch_open` come from the feed and are carried so the receiver can interpret
a suspicious bearing; the station does not act on them.

**`age_ms` floor.** The feed reports a measurement `latency` and `processing_time`
totalling ~0.4–0.6 s, so `age_ms` has a floor near half a second even for the
newest measurement.

### 10.4 Position and heading

- **Position** is taken from the KrakenSDR feed, may change, and is transmitted on
  change (FR-10.5) against a per-station reference position, in decimetres.
  Stationary GPS position jitter of ~3–4 m is observed, so `position_epsilon_dm`
  defaults to 50 (5 m). Re-basing the reference is a §8 configuration operation.
- **Heading** is a fixed 0° by manual array alignment (A3) and is **not transmitted**
  in v1.

A station whose array is physically rotated away from its assumed heading produces
bearings wrong by that angle, and no link or confidence metric reveals it. Alignment
and its verification are operator responsibilities (§1.2, §24.2).

### 10.5 No clocks

v1 produces no position fix, so no cross-station time alignment is required. Records
carry **age**, not an absolute timestamp; the Management Pi converts to absolute
time on arrival using its own clock. No clock synchronisation exists in v1.

### 10.6 Deferred: fixes

`hornethunter_shared.geo.triangulate()` and `intersect_bearings()` are implemented
and unit-tested, and `management_pi` exposes a `--fix-from` entry point. These are
**v2 seams, unused in v1**.

### 10.7 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| No DoA measurement ever received | Record streamed with `no_data`; station keeps streaming |
| Feed stalls mid-operation | `kraken_link_up` cleared, `age_ms` grows; ages beyond `max_age_ms` reported as `no_data` |
| `age_ms` would overflow `u16` (>65.5 s) | Clamped to `0xFFFF` and `no_data` set |
| Bearing outside 0..359.99° | Discarded, counted, logged; previous measurement retained |
| Station moved beyond ±3.2 km of its reference | Reference re-base required; flagged to the operator |

---

# Part B — Interfaces (L1)

## 11. HH-Link Frame Format & ARQ

### 11.1 Purpose and peer

The protocol between the Management Pi and the stations. Its peer is another HH-Link
endpoint. The frame format runs over the LoRa carrier — a transparent serial byte
pipe on the Pi, RFC2217 on the bench (§16.2). JSON is used in logs and tests; it is
never the wire form.

### 11.2 Frame format

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
  (§12.3). The receiver shall strip that trailing byte **before** validating the CRC.
- CRC-16/CCITT-FALSE, over VER..PAYLOAD. The LoRa PHY guarantees integrity (§16.3);
  this CRC guards against framing errors — mis-synchronisation, truncation,
  coalescing.

### 11.3 Message types

| Type | Name | Direction | ACKed | Payload |
|-----:|------|-----------|-------|---------|
| 0x1 | `BEACON` | master → all | **no — broadcast** | superframe timing-zero, live slot-map (station # → slot), config-pending target (§6.2) |
| 0x2 | `BEARING` | station → master | **no — streamed** (§6) | §10.3 record |
| 0x3 | `ACK` | master ↔ station | — | acked seq, config version, config CRC, status flags |
| 0x4 | `PARAM_DELTA` | master → station | yes | changed `(id, value)` entries |
| 0x5 | `PARAM_FULL` | master → station | yes | full set; fragmented |
| 0x6 | `PARAM_REQ` | master → station | yes | request full-set report |
| 0x7 | `PARAM_REPORT` | station → master | yes | full set; fragmented |
| 0x8 | `IDENT` | station → master | yes | agent version, schema version, capabilities |
| 0x9 | `JOIN` | station → master | **no — contention** | requesting station number, seeking a slot (§6.3) |

Only the configuration exchange (types 0x3–0x8) is acknowledged and retransmitted
(§11.5). `BEACON`, `BEARING` and `JOIN` are fire-and-forget: the `BEACON` is
broadcast (dest `0xFF`) each superframe and defines the schedule (§6.2); a `BEARING`
rides its assigned slot; a `JOIN` is sent in the beacon's contention window with
backoff (§6.3). Fragmented types carry `frag_index` and `frag_total` at the head of
the payload.

### 11.4 Requirements

- **FR-11.1** [Must] Frames shall be self-delimiting by sync word, length and CRC,
  and the receiver shall recover from arbitrary garbage without operator action.
- **FR-11.2** [Must] The frame format and the configuration ARQ (§11.5) shall be
  independent of the underlying byte carrier (serial device or RFC2217, §16.2).
  Bearings carry no ARQ (§6).
- **FR-11.3** [Must] Payload shall not exceed 200 bytes; larger messages shall be
  fragmented at the HH-Link layer (FR-11.6).
- **FR-11.4** [Must] The receiver shall strip a DTU-appended RSSI byte before
  CRC validation when RSSI reporting is enabled.
- **FR-11.5** [Must] Frames failing CRC shall be discarded and counted, never
  partially interpreted.
- **FR-11.6** [Must] `PARAM_FULL` and `PARAM_REPORT` shall support fragmentation
  with per-fragment acknowledgement.
- **FR-11.7** [Must] Duplicate frames — a retransmission whose ACK was lost — shall
  be detected by sequence number, acknowledged again, and **applied only once**.
- **NFR-11.1** [Must] The codec shall be a pure function of bytes, independent of
  the byte carrier and of wall-clock time.

### 11.5 ARQ — configuration path only

ARQ covers **only** the configuration exchange (§8): `PARAM_DELTA`, `PARAM_FULL`,
`PARAM_REQ`, `PARAM_REPORT` and their `ACK`s. Bearings are never acknowledged or
retransmitted (§6). Configuration uses **stop-and-wait**, one outstanding frame per
direction per station.

| Parameter | Default |
|-----------|---------|
| retransmission timeout | 1000 ms |
| maximum attempts | 5 (original plus four retransmissions) |
| sequence space | `u8`, wrapping |

The timeout must exceed a full round trip of the largest fragment: a ~126-byte
frame is ~215 ms on air each way (Appendix B), so the request, the reply and both
turnarounds approach a second — hence the 1000 ms default. The
carrier discards frames that fail their own PHY CRC (§16.3), so the link loses
frames but never delivers corrupted ones; sequence-plus-ACK-plus-retransmit is
sufficient. On exhausting attempts the configuration operation is abandoned and
reported to §8 — the change stays **unconfirmed and pending**, never silently
assumed applied (N4).

### 11.6 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Partial frame received | Retained in the reassembly buffer until complete or `frame_timeout` elapses, then discarded and counted |
| Garbage / lost sync | Byte-wise resynchronisation on the sync word; discarded bytes counted |
| Coalesced frames in one read | All complete frames extracted from the buffer in order |
| CRC failure | Discarded and counted (FR-11.5); a config frame is retransmitted by ARQ, a bearing is simply skipped (§6) |
| Duplicate delivery | Re-acknowledged, applied once (FR-11.7) |
| Fragment set incomplete | Whole set discarded after `frag_timeout`; sender retries the set |

---

## 12. LoRa DTU Provisioning Interface

### 12.1 Purpose and peer

Configures a locally attached SX1262 DTU over its AT command set, so that a node's
radio settings come from its configuration file.

Entering AT mode requires the escape terminated with CRLF — `+++\r\n`; a bare `+++`
produces no response. Verified on firmware Ver1.2; see
[lora-dtu-sx1262.md](lora-dtu-sx1262.md).

### 12.2 Requirements

- **FR-12.1** [Must] On startup the agent shall read the DTU's current parameters
  and compare them against its configuration.
- **FR-12.2** [Must] The agent shall write only parameters that differ, then leave
  AT mode with `AT+EXIT` so settings take effect.
- **FR-12.3** [Must] The agent shall verify by read-back every parameter it wrote,
  except write-only parameters (§12.4).
- **FR-12.4** [Must] Entering AT mode shall use `+++\r\n`; every command shall be
  CRLF-terminated.
- **FR-12.5** [Must] The agent shall guarantee `AT+EXIT` on every exit path,
  including on error, so a DTU is never left in AT mode.
- **FR-12.6** [Must] Radio parameters shall be applied exactly as configured, with
  no plausibility checking (§1.2, A2).
- **FR-12.7** [Should] Parameters shall be queried individually rather than parsed
  from `AT+AllP?`, whose field order differs between the vendor documentation and
  the shipped firmware.
- **NFR-12.1** [Must] Provisioning shall be idempotent and safe to re-run.

### 12.3 Parameters applied

| Command | Purpose | Note |
|---------|---------|------|
| `AT+MODE` | operating mode | `1` (transparent/stream) — required by §16.1 |
| `AT+ADDR` | node address | per §19.2 |
| `AT+TXCH`,`AT+RXCH` | channel | operator-owned (§1.2) |
| `AT+SF`,`AT+BW`,`AT+CR`,`AT+PWR` | radio parameters | operator-owned. `AT+BW` takes an **index** (`0`=125 kHz), not a value in kHz. `AT+PWR` range 10–22 dBm |
| `AT+LBT` | listen-before-talk | `0`; the master owns the schedule (§2.1) |
| `AT+RSSI` | append RSSI to received data | configurable; display-only (NFR-9.1). Affects framing (FR-11.4) |
| `AT+KEY` | AES key | **write-only**; cannot be read back (FR-12.3). Not a security control (§23.2) |

### 12.4 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| AT mode not entered | Provisioning abandoned; DTU left untouched and assumed already configured; logged as a startup warning; service continues |
| Read-back mismatch | Retried once, then logged as an error; the node continues with the actual value and reports it |
| `AT+KEY` set | Cannot be verified. Recorded as "written, unverifiable" |
| DTU absent | No operational link (§7); the agent keeps running and measuring, and the Management Pi shows the station `RED` (§9, §22.3) |
| Exit fails | Retried, then `AT+REBOOT` (present in firmware, absent from vendor documentation) |

---

## 13. Kraken DoA Source Interface

### 13.1 Purpose and peer

Consumes direction-of-arrival measurements from the KrakenSDR software on the same
host.

### 13.2 Protocol

The KrakenSDR DSP rewrites a single CSV file, `DOA_value.html`, on every DoA update
and its Node server (port **8081**) serves it over HTTP. The DSP writes this file for
**every** `doa_data_format` except `Kerberos App` (verified in
`_sdr/_signal_processing/kraken_sdr_signal_processor.py`), so no reconfiguration is
required — the deployed station's `Full POST` format already populates it. There is
**no** local WebSocket DoA feed on the KrakenSDR; the station **polls**:

```
GET http://127.0.0.1:8081/DOA_value.html   →   one CSV line per active VFO
```

Each line is positional (DSP field order); the station consumes the first five
fields and takes the last non-empty line:

```
ts_ms, bearing_deg, confidence, max_power_dBm, freq_Hz, array, latency_ms,
station_id, lat, lon, heading, heading, "GPS", R, R, R, R, <360° spectrum...>
```

`bearing_deg` is already compass convention — the DSP writes `360 - theta`.
`confidence` is not normalised to 0..1 (§10.3). The 360° spectrum tail is **not
consumed in v1**. An **empty** file is a valid state, not an error: it means the VFO
squelch is closed (no signal), so no bearing is produced and the master's staleness
health (§9) goes RED until a signal returns. A bearing is emitted only when the DSP
timestamp advances, so a static file is never re-reported.

### 13.3 Backends

One internal measurement type, three sources, selected by configuration:

| Backend | Use | Transport |
|---------|-----|-----------|
| `kraken` | real hardware | HTTP `GET :8081/DOA_value.html` (CSV) |
| `simulator` | `KrakenSimulator` | HTTP `GET /api/v1/doa` (JSON) |
| `synthetic` | host-tier tests, no hardware | in-process generator |

The simulator emits JSON keyed by name; the real KrakenSDR emits positional CSV. A
name-keyed adapter maps the simulator record; the CSV is parsed by field position:

| | simulator (JSON key) | real (CSV position) |
|---|---|---|
| bearing | `bearing_deg` | field 1 (`360 - theta`) |
| quality | `width_rad` | field 2 (`confidence`) |
| power | `rssi_dbfs` | field 3 (`max_power_dBm`) |
| frequency | `center_freq_hz` | field 4 (`freq_Hz`) |
| transport | HTTP JSON poll | HTTP CSV poll |

### 13.4 Requirements

- **FR-13.1** [Must] The station shall poll the DoA feed and recover automatically
  with backoff when the endpoint is unreachable.
- **FR-13.2** [Must] Feed availability shall be exposed as an explicit state, and
  reported in every bearing record (FR-10.6).
- **FR-13.3** [Must] All three backends shall present one internal type; no caller
  shall branch on backend.
- **FR-13.4** [Must] Malformed or unparseable records shall be discarded and
  counted, never propagated as bearings.
- **FR-13.5** [Should] The station shall tolerate any `doa_data_format` except
  `Kerberos App` (the only value that does not populate `DOA_value.html`); no
  reconfiguration of the KrakenSDR is required to read bearings.
- **NFR-13.1** [Must] Adapter mapping shall be pure and table-driven, testable
  without a network.

### 13.5 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| HTTP endpoint refuses/unreachable | Retry with capped exponential backoff; feed state down; heartbeats still streamed |
| Endpoint reachable but file empty | Valid no-signal state (squelch closed); no bearing produced, master goes RED per §9 |
| Malformed or short CSV line | Record discarded and counted (FR-13.4) |
| `doa_data_format` set to `Kerberos App` | Feed silent (the one format that skips `DOA_value.html`). Detected as feed-down; §8 read-back reveals the cause |
| Feed faster than the bearing rate limit | Latest wins; discard count reported (FR-10.4) |

---

## 14. Kraken Settings Interface

### 14.1 Purpose and peer

Reads and writes the KrakenSDR configuration on the **local** host. The KrakenProxy
is co-located with the KrakenSDR software (§5), so both
directions are localhost and neither depends on any remote-control feature.

**Write — direct settings file.** The vendor-documented programmatic-config path is
an **atomic edit of `_share/settings.json` with `ext_upd_flag` set true**: the DSP's
0.5 s watcher (§14.3) picks up the changed file and applies it in place. The
KrakenSDR's own node middleware applies remote settings the identical way
(`fs.writeFileSync` of the same file). This needs **no `en_remote_control`, no HTTP
upload route, and no cloud** — it always works because the KrakenProxy runs on the
same host and owns the file. The write is **atomic** (write a temporary file, then
`rename`) so it never races the watcher mid-read.

**Read / read-back — HTTP.** Settings are read over `GET
http://127.0.0.1:8081/settings.json`, and re-read after every write to compute the
canonical CRC from what the DSP **actually holds** (FR-8.5), not from what was sent.

The HTTP *write* routes upstream documents are **not** used: `:8081/upload` exists
only when `en_remote_control` is enabled (404 otherwise), and `:8042/settings` is
absent on the deployed build. The file write supersedes both, removing the
commissioning prerequisite they would impose.

### 14.2 Requirements

- **FR-14.1** [Must] The KrakenProxy shall apply a delta by reading current
  settings, merging the changed fields, and writing the merged result back to
  `_share/settings.json` **atomically** (temp file + `rename`) with `ext_upd_flag`
  set true.
- **FR-14.5** [Must] Configuration writes shall use the local settings-file path and
  shall not depend on `en_remote_control` or any HTTP write route.
- **FR-14.6** [Must] If the settings file cannot be written (missing path, permission
  loss), the KrakenProxy shall report the station **config-unwritable** with a
  distinct reason the UI can display, shall still serve configuration read-back for
  divergence detection (§8.5), and shall reject parameter pushes rather than fail.
- **FR-14.2** [Must] After every write the agent shall re-read settings and compute
  the canonical CRC from that read-back (FR-8.5).
- **FR-14.3** [Must] The agent shall report every field the KrakenSDR altered,
  clamped, or ignored relative to what was requested.
- **FR-14.4** [Must] The agent shall remain fully operational when this interface is
  unreachable (§2.3).
- **NFR-14.1** [Must] A parameter application shall complete within
  `param_apply_timeout_s` or be reported as failed, never left indeterminate.

### 14.3 Application is live

The KrakenSDR software watches its settings file on a **0.5 s timer** and applies
changes in place, including retuning the receiver when the centre frequency changes.
There is no service restart. The parameter push is an ordinary operation with no
"station is blind" state in the UI.

### 14.4 All settings are exposed

Every field is operator-editable (§15.3). This is safe because:

- the KrakenProxy is independent of KrakenSDR health (§2.3), so no setting can
  take away the channel needed to undo it;
- the Management Pi retains the previous accepted snapshot (§8.6).

The UI **warns** on fields the system depends on — `doa_data_format` (silences the
feed), `default_ip` and `data_interface` (break the DAQ chain) — but does not
prevent their change.

### 14.5 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Endpoint unreachable | Delta not applied; ACK carries `kraken_down`; CRC not asserted; distinguished from divergence (§8.7) |
| Write accepted but read-back differs | Reported per FR-14.3; surfaces as `CONFIG_DIVERGED` at the Management Pi |
| Settings file unwritable (path/permission) | Station reported **config-unwritable** (FR-14.6); reads still served |
| Malformed settings written | The DSP performs no schema validation. Mitigated by revert (§8.6) |

---

## 15. Management UI Interface

### 15.1 Purpose and peer

A browser-facing interface on the Management Pi. Its peer is the operator.

### 15.2 Protocol and assets

- HTTP for the page and for operator actions; a **WebSocket** pushes live values.
- **All assets are served locally** (A5). v1 displays numbers only (N2), so no
  charting or mapping library is required.

### 15.3 Requirements

- **FR-15.1** [Must] The UI shall display, per station: bearing, confidence, power,
  measurement age, discard count, health state, time since last bearing, bearing
  rate, last RSSI, `config_version` and configuration state.
- **FR-15.2** [Must] Values shall be **numeric**; v1 shall provide no graphical
  bearing display, plot, or map.
- **FR-15.3** [Must] The UI shall expose **every** KrakenSDR settings field,
  organised into panels mirroring the KrakenSDR software's own grouping.
- **FR-15.4** [Must] The UI shall warn on fields the system depends on (§14.4)
  without preventing their modification.
- **FR-15.5** [Must] Editing a field shall transmit a delta (FR-8.1), never a full
  set.
- **FR-15.6** [Must] The UI shall provide explicit *Read full settings* and *Push
  full settings* actions per station (FR-8.8).
- **FR-15.7** [Must] The UI shall provide a *revert to last known good* action per
  station (§8.6).
- **FR-15.8** [Must] The UI shall display a live tail of the debugging log (§21).
- **FR-15.9** [Must] Health and configuration state shall be shown as **separate**
  indicators (FR-9.6).
- **FR-15.10** [Must] The UI shall provide a **target selector** for configuration,
  defaulting to **All stations**. Editing a field (FR-15.5) or a full push (FR-15.6)
  shall fan out to every live station as an individual per-station ARQ exchange
  (§11.5), or to a single station when one is selected.
- **FR-15.11** [Must] The UI shall show the connected-station network from the
  master's live-set (§6): each station's live/stale state, assigned slot, health
  (§9), and configuration-sync state. An offline/unreachable station shall be flagged
  **RED** with a **pending-sync** indication until it rejoins and confirms by
  read-back CRC (§8.5).
- **FR-15.12** [Must] The operator shall be able to **retire (delete)** a RED station
  for the session, freeing its slot (§6) and clearing its queued configuration; a
  retired station that transmits again rejoins as new (§6.3).
- **NFR-15.1** [Should] Displayed values shall update within 250 ms of arrival.

### 15.4 Panels

Grouped as the KrakenSDR software groups them; its documentation is vendored at
[krakensdr-wiki/](krakensdr-wiki/).

| Panel | Content |
|-------|---------|
| Stations | live numeric rows, health, assigned slot, config-sync state; config **target selector** (default All); **retire** action for a RED station |
| RF Receiver | `center_freq`, `uniform_gain`, `data_interface`, `default_ip` |
| DoA Configuration | `en_doa`, `ant_arrangement`, `ula_direction`, `ant_spacing_meters`, `custom_array_x/y_meters`, `array_offset`, `doa_method`, `doa_decorrelation_method`, `expected_num_of_sources` |
| Display Options | `doa_fig_type`, `en_peak_hold`, `compass_offset` |
| VFO Configuration | `spectrum_calculation`, `vfo_mode`, `active_vfos`, `output_vfo`, `dsp_decimation`, `en_optimize_short_bursts` |
| VFO 0–15 | `vfo_freq_N`, `vfo_bw_N`, `vfo_squelch_N`, `vfo_squelch_mode_N`, `vfo_demod_N`, `vfo_iq_N`, `vfo_fir_order_factor_N` |
| Station Information | `station_id`, `location_source`, `latitude`, `longitude`, `heading`, `doa_data_format`, `krakenpro_key`, `rdf_mapper_server` |
| Recording / System | `en_data_record`, `write_interval`, `logging_level`, `en_hw_check`, `disable_tooltips` |
| Link | staleness threshold, bearing rate limit, log tail |

Field types, units and ranges come from the field registry (§8.3), so the form is
generated from data. `center_freq` is in **MHz** while `vfo_freq_N` is in **Hz**.

### 15.5 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| WebSocket drops | Client reconnects; values marked stale rather than frozen at last value |
| Station unreachable | Flagged **RED** with pending-sync; row retained with last values and age; queued config reconciles on rejoin (FR-15.11–FR-15.12) |
| Operator submits an out-of-type value | Rejected client-side against the registry type before transmission |
| Two operators editing at once | Last write wins; both changes appear in the log. Not defended against in v1 |

---

# Part C — Foundation (L0)

## 16. LoRa DTU & Byte Carriers

### 16.1 Purpose and division of responsibility

The SX1262 DTU is a vendor device used as a **transparent byte pipe** (`AT+MODE=1`).
We configure it (§12) and rely on its documented behaviour.

| We own | The device owns |
|--------|-----------------|
| Framing, addressing, reliability, scheduling | Modulation, PHY CRC, packetisation, AES, caching |

### 16.2 Carrier abstraction

Two byte carriers behind one interface, so nothing above §11 knows which is in use.
Both are LoRa — WLAN is not a byte carrier (§7):

| Carrier | Used for |
|---------|----------|
| local serial device | LoRa on a Pi |
| RFC2217 network serial | LoRa on the development bench |

### 16.3 Properties relied upon

- **Payload CRC cannot be disabled**, and a packet failing it is dropped rather than
  delivered. The link **loses frames but never corrupts them** (§11.5).
- **Maximum single packet is 240 bytes**; larger writes are auto-packetised. Frames
  are capped at 209 B (§11.2) to stay inside one packet.
- **Transparent mode has no local echo** — bytes written to a DTU do not return on
  that port. Diagnostics read the *peer*.
- **First-packet warm-up loss**: the first one or two transmissions after opening a
  fresh connection may be dropped. Startup shall send and discard a throwaway frame.
- 960-byte internal cache and auto-packetisation are fixed and not configurable.

### 16.4 Requirements

- **NFR-16.1** [Must] No component above §11 shall depend on which carrier is in use.
- **NFR-16.2** [Must] Startup shall absorb warm-up loss before the first real frame.
- **NFR-16.3** [Must] Resolution of a serial device shall be by stable identity — a
  `/dev/serial/by-id/` path on a Pi (§2.2), or a workbench slot label on the bench —
  not by `ttyACM` index, which is not stable across enumerations.

### 16.5 Failure modes

Exercised **transitively** through §11 and §12 — this layer has no tests of its own.

| Condition | Behaviour |
|-----------|-----------|
| Port disappears (unplug) | Carrier marked down; reopen with backoff; the Management Pi shows the station `RED` until the port returns (§7, §9) |
| RFC2217 proxy already has a client | One client per port only. Reported as carrier-unavailable |
| Device left in AT mode | Data does not flow. §12.4 exit guarantee prevents; recovery is `AT+REBOOT` or power cycle |

---

## 17. krakensdr_doa & Middleware

### 17.1 Purpose and division of responsibility

External software on the station host. We configure it and consume its outputs.

| We own | It owns |
|--------|---------|
| Which settings we write; how we parse its outputs | DSP, DoA estimation, its settings watcher, its middleware |

### 17.2 Services and DoA formats

The KrakenSDR software exposes these local services:

| Port | Service | Role |
|------|---------|------|
| 8080 | Dash UI | operator web UI; browser only |
| 8021 | — | no DoA WebSocket is opened |
| 8042 | Express middleware | running; `/settings` route not present (§14.1) |
| 8081 | Node / miniserve | serves `settings.json` and `DOA_value.html` (§13.2, §14.1) |

`doa_data_format` selects what the DSP emits. Any value except `Kerberos App`
populates `DOA_value.html` (§13.2). Two values carry side effects:

- **`Full POST`** issues a blocking `GET https://ip.seeip.org/jsonip?` once per
  second inside the processing loop; with no internet this stalls the DSP thread.
- **`Kraken Pro Remote`** relays measurements to a third-party cloud
  (`wss://map.krakenrf.com`).

**`Kraken App`** writes the same `DOA_value.html` with no outbound request and is the
recommended production format.

The settings schema is read from the live station, not an upstream sample: `en_fbavg`
is absent; decorrelation is `doa_decorrelation_method` (`Off` / `FBA` / `TOEP` /
`FBSS`).

### 17.3 Lifecycle and gating

Our agent and this software start independently. **The agent shall not gate its own
startup on the software being present or healthy** (§2.3, FR-14.4): it starts,
streams bearings, and reports the feed as down.

### 17.4 Requirements

- **NFR-17.1** [Must] The agent shall tolerate this software being absent, stopped,
  restarted, or misconfigured at any time, without the agent restarting.
- **NFR-17.2** [Must] Version-sensitive coupling shall be confined to §13 and §14.

### 17.5 Failure modes

Exercised transitively through §13 and §14. Relied-upon upstream behaviours — the
middleware services (§17.2), the `Kraken Pro Local` fan-out, the 0.5 s settings
watcher — are covered by the simulator backend.

---

## 18. Host Platform

### 18.1 Purpose and division of responsibility

Raspberry Pi OS, systemd, the network stack, the filesystem, and Python. We
configure units, paths and dependencies.

### 18.2 Requirements

- **NFR-18.1** [Must] Each node shall run as a single systemd service, restarting
  automatically on failure.
- **NFR-18.2** [Must] Configuration shall live outside the repository, in
  `/etc/hornethunter/`.
- **NFR-18.3** [Must] Logs shall be written to a local path with size-bounded
  rotation (§21.3).
- **NFR-18.4** [Must] A node shall reach a working state after an unattended power
  cycle with no operator action.

### 18.3 Failure modes

| Condition | Behaviour |
|-----------|-----------|
| Service crash | systemd restarts; restart counted and logged; bearings resume, RED clears |
| Disk full | Rotation bounds log growth (§21.3); logging degrades before operation does |
| Clock jumps (no NTP) | Tolerated: v1 uses no absolute cross-node time (§10.5). Log timestamps record monotonic time alongside wall-clock |

### 18.4 Field network — isolated access point

The field WLAN is the **out-of-band setup and management network** (§7), not an
operational carrier: SSH provisioning and log retrieval ride it, HH-Link does not.
The Management Pi is its access point **and** its only gateway to
the outside. It runs a NetworkManager AP-mode connection on `wlan0` for the
`192.168.50.0/24` field subnet; NetworkManager supplies the access point (its
hostapd backend) and DHCP/DNS (its internal dnsmasq) via `ipv4.method shared`, and
`shared` also enables NAT masquerading from the field subnet out the default route
(`eth0`). No `hostapd` or `dnsmasq` packages are installed. NetworkManager is the
authoritative store: connections created with `nmcli` are mirrored automatically
into `/etc/netplan/90-NM-*.yaml`, so the `nmcli` commands below are the source of
truth. Verified platform: Debian 13 (trixie), NetworkManager 1.52.

The field subnet is isolated. Stations live on `192.168.50.0/24` only; they are not
bridged to the development network and, behind the Management Pi's NAT, cannot be
reached from it. `eth0` on the Management Pi is the sole outside connection and is
for development access only. Each station keeps its wired `eth0` for emergency
recovery, independent of `wlan0`.

- **NFR-18.5** [Must] The Management Pi shall run a NetworkManager AP-mode
  connection on `wlan0` with `ipv4.method shared`, autostarting at boot.
- **NFR-18.6** [Must] Each station shall associate to that SSID via a
  NetworkManager client connection, autostarting at boot, and receive a fixed
  `192.168.50.x` lease keyed by its `wlan0` MAC (§19).
- **NFR-18.7** [Must] The field subnet `192.168.50.0/24` shall not overlap the
  development uplink subnet.
- **NFR-18.8** [Must] The field subnet shall be isolated: a station shall not be
  directly reachable from the development network, its only route outward being NAT
  through the Management Pi's `eth0`.
- **NFR-18.9** [Must] Every station shall be reachable from the Management Pi by SSH
  over the field WLAN, authenticated by a dedicated fleet key (see *Fleet SSH
  access* below).
- **NFR-18.10** [Must] Each station shall retain a wired `eth0` recovery path,
  independent of `wlan0` and of the Management Pi.

| Parameter | Value |
|-----------|-------|
| interface | `wlan0` access point; `eth0` development uplink / NAT gateway |
| mechanism | NetworkManager connection, `802-11-wireless.mode ap`, `ipv4.method shared` (DHCP + NAT) |
| AP address / subnet | `192.168.50.1/24` |
| station leases | NetworkManager shared DHCP over `192.168.50.0/24`; fixed per station |
| station addresses | `kraken-01` → `192.168.50.101`, `kraken-02` → `192.168.50.102` |
| band | 2.4 GHz (`bg`) |
| channel | operator-chosen (NetworkManager auto by default) |
| SSID / passphrase | `HornetAP`, WPA2-PSK (`wpa-psk`), operator-set |
| management access | SSH from the Management Pi over `wlan0`, fleet key `~/.ssh/hornethunter_fleet` (station user `krakenrf`) |
| regulatory domain | `CH` on both nodes (`raspi-config nonint do_wifi_country CH`) — required for the AP to select a channel |
| AP channel | pinned to `6` (2.4 GHz, universally associable; NetworkManager otherwise auto-selects 13) |

**Reproducible setup — Management Pi (access point + NAT gateway):**

```bash
sudo raspi-config nonint do_wifi_country CH   # regulatory domain; unblocks Wi-Fi
sudo rfkill unblock wlan
nmcli con add type wifi ifname wlan0 con-name hh-ap ssid "HornetAP" autoconnect yes
nmcli con modify hh-ap \
  802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<passphrase>" \
  ipv4.method shared ipv4.addresses 192.168.50.1/24 \
  connection.autoconnect-priority 100
nmcli con up hh-ap
```

The channel is pinned to `6`; left on auto, NetworkManager may pick channel 13,
which many clients will not associate with.

`ipv4.method shared` starts NetworkManager's dnsmasq (DHCP + DNS on the subnet) and
installs the NAT masquerade rule out the default route — no separate router config.
Fixed station leases are pinned with a dnsmasq drop-in read by NetworkManager's
instance:

```bash
# /etc/NetworkManager/dnsmasq-shared.d/hornethunter-leases.conf
dhcp-host=<kraken-01-wlan0-mac>,192.168.50.101,kraken-01
dhcp-host=<kraken-02-wlan0-mac>,192.168.50.102,kraken-02
```

**Reproducible setup — each station (associate to the access point):**

```bash
sudo raspi-config nonint do_wifi_country CH
nmcli con add type wifi ifname wlan0 con-name hh-field ssid "HornetAP" autoconnect yes
nmcli con modify hh-field wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<passphrase>" \
  connection.autoconnect-priority 100
# Field isolation: HornetAP is the only Wi-Fi the station will auto-join, and the
# wired eth0 never carries the default route (recovery / bench UI access only).
nmcli -t -f UUID,TYPE,AUTOCONNECT,NAME con show | \
  awk -F: '$2=="802-11-wireless" && $3=="yes" && $4!="hh-field"{print $1}' | \
  xargs -rn1 -I{} nmcli con modify {} connection.autoconnect no
nmcli con modify "Wired connection 1" ipv4.never-default yes ipv4.route-metric 700
nmcli con up hh-field
```

The station receives its fixed `192.168.50.x` lease and a default route via
`192.168.50.1`; the Management Pi reaches the station by SSH over this address when
associated (NFR-18.9), for setup, management and log retrieval (§7). The wired
`eth0` carries no default route — it is a recovery / bench path only (§2.2,
NFR-18.10). The station's operational bearing and configuration traffic always rides
the LoRa link (§7), never the WLAN.

**Fleet SSH access.** Because the field subnet is not routed to the development
network, a development host reaches a station only by hopping through the Management
Pi: `dev host → pi@<management-eth0> → ssh kraken-0N`. The Management Pi holds a
dedicated, passphrase-less fleet key `~/.ssh/hornethunter_fleet` whose public key is
authorized for user `krakenrf` on every station, and an `~/.ssh/config` that maps
each station name to its fixed field address:

```text
# ~/.ssh/config on the Management Pi
Host kraken*
    User krakenrf
    IdentityFile ~/.ssh/hornethunter_fleet
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new

Host kraken-01 kraken1
    HostName 192.168.50.101
#Host kraken-02 kraken2
#    HostName 192.168.50.102
```

So `ssh kraken-01` from the Management Pi lands on the station over the field WLAN.
The Management Pi's own address is a DHCP lease on the development LAN (currently
`192.168.0.27`). Adding a station: assign its fixed lease (the `dhcp-host` drop-in
above), append a matching `Host kraken-0N` block with its `192.168.50.x`, and
install the fleet public key on the station —
`ssh-copy-id -i ~/.ssh/hornethunter_fleet.pub krakenrf@<station>`. The fleet private
key lives only on the Management Pi and must be regenerated and re-copied after the
Management Pi is re-imaged (its `~/.ssh` does not survive a fresh flash).

---

# Part D — Cross-cutting Concerns

## 19. Identity & Addressing

### 19.1 Requirements

- **FR-19.1** [Must] Each node shall have a stable identity: a station id used in
  the protocol and a human-readable name used in the UI and logs.
- **FR-19.2** [Must] HH-Link addresses shall be independent of DTU hardware
  addresses, so a dongle can be swapped without a protocol change.
- **FR-19.3** [Must] The Management Pi shall reject and count frames from
  unconfigured station ids (FR-6.9).
- **FR-19.4** [Must] A station's number (its §19.2 address) shall be its scheduling
  identity: the master shall map live stations to TDMA slots in ascending number
  order, compacting so a not-live number reserves no slot (§6.2, FR-6.6).

### 19.2 DTU addressing

**All nodes share one channel with the DTU address flat (`AT+ADDR=0`).** Per-node
addressing is done at the HH-Link layer — the DEST/SRC bytes (§11.2) — not at the
DTU: every DTU on the channel physically hears every transmission, and each node
keeps the frames addressed to it and ignores the rest.

The distinct-address scheme the DTU documents (master `0xFFFF`, stations `0x000n`,
to make stations mutually deaf) is **not used**: measured on our hardware it degrades
the link (received bearing rate fell from ~1.3 Hz to ~0.3 Hz and the station went
stale), whereas flat `AT+ADDR=0` is reliable. The master-scheduled TDMA does not need
DTU-level mutual deafness: exclusive slots (§5, §6) prevent collisions, and a station
discards frames not addressed to it. A station hearing another's transmissions is
harmless — the hidden-node assumption (FR-7.4) is about what a node **cannot** rely on
hearing, not about enforced deafness.

The beacon and any master→all frame are HH-Link broadcasts (DEST `0xFF`, §11.2),
reaching every node in one transmission on the shared channel.

### 19.3 Slot mapping

The station number that addresses a station (§19.2) is also its **scheduling
identity**. The master's beacon (§6.2) lists the live stations in ascending number
order and gives each the next data slot, so a station's slot is its **rank among the
live set**, not a fixed function of its number: a not-live number reserves nothing
and the survivors compact upward, freeing slots for a higher rate (§6.2). Slot
ownership is beacon-relative and changes as stations join and leave; a station always
reads its current slot from the latest beacon, never from a static table.

---

## 20. Configuration Catalog

### 20.1 Requirements

- **FR-20.1** [Must] All tunables shall be declared in configuration with documented
  defaults; none shall be hard-coded at a call site.
- **FR-20.2** [Must] The health staleness threshold and the bearing rate limit shall be
  changeable at runtime without restart (NFR-9.2).
- **FR-20.3** [Must] Radio parameters shall be applied verbatim, without
  plausibility checking (A2).

### 20.2 Catalog

| Group | Keys |
|-------|------|
| identity | `station.id`, `station.name` |
| link | `link.address`, `link.channel`, `link.sf`, `link.bw`, `link.cr`, `link.power`, `link.lbt`, `link.rssi_append`, `link.key` |
| carrier | `carrier.serial_url` |
| stream | `stream.max_rate_hz` |
| arq | `arq.timeout_ms`, `arq.max_attempts`, `arq.frame_timeout_ms`, `arq.frag_timeout_ms` (config path only, §11.5) |
| health | `health.staleness_threshold_s`, `health.orange_rate_fraction`, `health.rate_window_s` |
| kraken | `kraken.backend`, `kraken.doa_url`, `kraken.settings_url`, `kraken.param_apply_timeout_s` |
| bearing | `bearing.position_epsilon_dm`, `bearing.max_age_ms`, `bearing.reference_lat`, `bearing.reference_lon` |
| logging | `log.path`, `log.max_bytes`, `log.backup_count`, `log.level` |
| ui | `ui.listen`, `ui.port` |

Defaults are in Appendix C.

---

## 21. Logging & Observability

### 21.1 Purpose

The log shall be sufficient to reconstruct after the fact why the link or the Kraken
interface behaved as it did.

### 21.2 Requirements

- **FR-21.1** [Must] Each node shall write structured **JSONL**, one object per
  event, machine-parseable without regular expressions.
- **FR-21.2** [Must] Every transmitted and received frame shall be logged with
  direction, type, source, destination, sequence, length, attempt number, round-trip
  time, CRC result, and RSSI when available.
- **FR-21.3** [Must] Every health state transition and configuration ARQ exhaustion
  shall be logged with its cause.
- **FR-21.4** [Must] Every parameter operation shall be logged with the fields
  changed, `config_version`, expected and observed CRC, and read-back differences.
- **FR-21.5** [Must] Kraken feed connects, drops, malformed records, discard counts
  and `adc_overdrive` transitions shall be logged.
- **FR-21.6** [Must] **Logs shall never be transmitted over the LoRa carrier.**
- **FR-21.7** [Must] Every record shall carry both wall-clock and monotonic
  timestamps (§18.3).
- **NFR-21.1** [Must] Logging shall never block bearing ingest or the config exchange; it shall drop
  records and count the drops in preference to stalling.

### 21.3 Retrieval

Logs are local files, rotated by size. They are retrieved **over WLAN** — the
out-of-band management path (§7) — when co-located, or read on the node. They are
never sent over LoRa (FR-21.6).

---

## 22. Error Handling & Safe States

### 22.1 Principle

**Indicate, do not conceal.** A fault that cannot be resolved automatically is
surfaced and left for a human (N4).

### 22.2 Requirements

- **FR-22.1** [Must] No fault shall be silently retried indefinitely. Retries shall
  be bounded, counted, and reported.
- **FR-22.2** [Must] Automatic remediation shall be **bounded, logged, and
  escalating**. Two classes exist: (a) the single full-set configuration push on CRC
  mismatch (FR-8.7); (b) the KrakenProxy's field autorecovery of a stalled KrakenSDR,
  a lost DoA feed, or a lost beacon (§5) — each attempt counted
  and logged, capped at a bounded retry budget with backoff. A fault that survives
  its budget shall be **indicated** (RED + reason), never silently retried.
- **FR-22.3** [Must] Absence of data shall be represented explicitly. A stale value
  shall never be presented as current (§10.7, §15.5).
- **FR-22.4** [Must] A node shall degrade rather than exit: loss of the Kraken feed
  or the DTU / LoRa link shall reduce function, not terminate the service.
- **FR-22.5** [Must] Counters — discards, CRC failures, resyncs, restarts,
  unconfigured-station frames, dropped log records — shall be exposed in the UI, not
  only in the log.

### 22.3 Safe states

| Subsystem | Safe state |
|-----------|-----------|
| Station, no Kraken feed | Streams `no_data` |
| Station, LoRa link down | Continues measuring; the Management Pi shows `RED` |
| Management Pi, station silent | Retains last values, greyed with age; keeps listening |
| Configuration diverged | Latched after one resync attempt; no further automatic change |
| DTU unconfigurable | Assume pre-configured, warn, continue |

---

## 23. Security

### 23.1 Scope

The threat model for v1 is **accidental**, not adversarial: the system shall not
silently accept malformed, duplicated, stale, or wrongly-addressed data. Protection
against a deliberate attacker on the RF medium is out of scope for v1.

### 23.2 What is and is not a control

- **Integrity within the system**: framing CRC (§11.2), sequence numbers and
  duplicate suppression (FR-11.7), address filtering (FR-19.3), and the
  configuration CRC (§8.4).
- **`AT+KEY` is not a security control.** The device offers AES keyed by a **16-bit**
  value, and the key is write-only, so it cannot be audited. It may be set as a
  network separator. It shall not be described as providing confidentiality (A6).
- **No confidentiality of bearings** is provided or claimed.

### 23.3 Requirements

- **NFR-23.1** [Must] Malformed or wrongly-addressed frames shall be discarded and
  counted, never partially applied.
- **NFR-23.2** [Must] A replayed frame shall not be applied twice (FR-11.7).
- **NFR-23.3** [Must] The management UI shall bind to an operator-configured
  interface, defaulting to the local network, not to a public interface.
- **NFR-23.4** [Must] Secrets present in the KrakenSDR settings — notably
  `krakenpro_key` — shall not be written to the debugging log.
- **NFR-23.5** [May] App-layer authentication of configuration frames is deferred;
  if added, a shared-secret truncated MAC over the frame is the intended mechanism
  (§3.3).

---

# Part E — Operations & Verification

## 24. Operational Procedures

### 24.1 Deploy

1. Provision each Pi by sparse-checkout of its own target — see
   [deployment.md](deployment.md).
2. Place configuration in `/etc/hornethunter/` (§20.2, §18.2).
3. Install and enable the systemd unit (§18.2).

### 24.2 Commission a station

1. Operator performs all RF setup — frequency, array geometry, spacing, and
   **manual alignment of the array to 0° heading** (§1.2, A3). A misalignment
   becomes a silent bearing error (§10.4); verify it by independent means.
2. No remote-control setup is required: the KrakenProxy writes configuration
   directly to the local settings file (§14.1). Any `doa_data_format` except
   `Kerberos App` serves the DoA feed (§13.2); no change is required.
3. Start the node; the agent provisions its DTU (§12) and connects to the Kraken
   feed (§13).
4. From the Management Pi, perform a manual **full-set read** (FR-8.8) — a station
   with no mirror entry has no baseline for deltas (§8.7).
5. Confirm the station reports `GREEN` (§9.3) and that bearings arrive with
   plausible age.

### 24.3 Operate

Watch the numeric rows and the two independent indicators — link health (§9.3) and
configuration state (§8.5). Bearings and configuration ride the LoRa link (§7).

### 24.4 Reconfigure

Edit a field in the UI (§15.3); a delta is sent (§8), applied live (§14.3), and
confirmed by read-back CRC (§8.5). Use *revert to last known good* (FR-15.7) if a
change misbehaves.

### 24.5 Recover

| Symptom | Path |
|---------|------|
| Station `RED` | §9, §22.3 — LoRa link, then power and antenna |
| `CONFIG_DIVERGED` latched | §8.7 — inspect read-back differences in the log, then manual full push |
| No bearings, link healthy | §13.5 — Kraken feed, then `doa_data_format` |
| Station config-unwritable, pushes rejected | §14.1 — check the settings-file path and permissions |
| Bearings implausible but link and config healthy | Operator-owned RF domain (§1.2): alignment, array, squelch |
| DTU passes no data | §16.5 — possibly stuck in AT mode; `AT+REBOOT` or power cycle |

---

## 25. Verification & Validation

### 25.1 Test architecture

Three tiers, cost-ordered. Each behaviour is tested at the **lowest tier where its
bug can manifest**.

| Tier | Environment | Speed | Covers |
|------|-------------|-------|--------|
| **host** | pure Python, no hardware, no network | ms | frame codec, CRC, RSSI-byte stripping, fragmentation, ARQ state machine under injected loss, delta computation, canonical config encoding, health state machine, bearing encode/decode, adapter field mapping |
| **bench** | Universal Embedded Workbench, two real DTUs on RFC2217, plus `simulator` or `synthetic` DoA source | s–min | DTU provisioning over AT, real transparent-mode framing including split and coalesced packets, bearing streaming and rate limiting, end-to-end parameter push with read-back, sustained-run stability |
| **field** | real antennas, real separation, real KrakenSDR | hours | link reliability at range, health threshold calibration, everything whose behaviour depends on link margin |

**Layer-to-tier mapping.** L2 application logic is pure and tested at the host tier.
L1 interfaces are split — pure core at the host tier, wire and flow behaviour at the
bench tier. L0 foundation has **no tests of its own** and is exercised transitively
through the L1 chapters that use it (§16.5, §17.5).

Measured airtime on the bench is identical with dummy loads and with antennas
(Appendix B); reliability is not. Every reliability threshold in §9.4 is provisional
until calibrated at the field tier.

### 25.2 Acceptance tests

| ID | Objective | Tier | Requirements |
|----|-----------|------|--------------|
| AT-1 | Codec round-trips every message type, including maximum payload and fragmented sets | host | FR-11.1, FR-11.3, FR-11.6 |
| AT-2 | ARQ delivers all frames at 0 %, 10 % and 50 % injected loss; reports exhaustion at 100 % | host | FR-11.7, §11.5, FR-22.1 |
| AT-3 | Split, coalesced and garbage-prefixed byte streams are recovered without loss of valid frames | host | FR-11.1, §11.6 |
| AT-4 | RSSI-append byte is stripped before CRC validation; frames validate with append on and off | host | FR-11.4 |
| AT-5 | Canonical CRC is stable across JSON reformatting and float re-serialisation, and unaffected by GPS-mutated fields | host | FR-8.4 |
| AT-6 | Health produces GREEN / ORANGE / RED for constructed bearing-arrival sequences (fresh, sparse, stale past the threshold) | host | FR-9.1–FR-9.5 |
| AT-7 | Simulator and real record shapes both map to the internal type | host | FR-13.3, NFR-13.1 |
| AT-8 | DTU provisioning is idempotent; AT mode is always exited, including on injected error | bench | FR-12.2, FR-12.5, NFR-12.1 |
| AT-9 | Two stations stream bearings for one hour; no station goes stale (RED) except on injected outages, and gaps are accounted for in the log | bench | FR-6.1–FR-6.4, NFR-21.1 |
| AT-10 | Single-field change reaches a station, applies live, and is confirmed by read-back CRC | bench | FR-8.1, FR-14.1, FR-14.2 |
| AT-11 | Externally corrupted station configuration raises `CONFIG_DIVERGED`, is repaired by exactly one automatic full push, and latches if corrupted again | bench | FR-8.7 |
| AT-12 | A LoRa link interruption during a config exchange leaves the in-flight transaction pending, not lost; it completes when the link returns and bearings resume | bench | NFR-7.1, FR-22.1 |
| AT-13 | Station keeps streaming `no_data` with the KrakenSDR software stopped | bench | FR-14.4, NFR-17.1, FR-10.6 |
| AT-14 | Management restart resumes delta operation from the persisted mirror with no full push | bench | FR-8.9 |
| AT-15 | Link stays out of `ORANGE` for a sustained run at intended deployment range | field | §9.3 |
| AT-16 | Health thresholds calibrated against measured retry behaviour at range | field | §9.4, NFR-9.2 |

### 25.3 Traceability

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
| PHY CRC | always on | cannot be disabled (§16.3) |
| UART | 115200 8N1 | open with DTR and RTS deasserted |
| Sync word | `0xA5 0x5A` | §11.2 |
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

Derived frame sizes: `BEARING` ≈ 19 B (23 B with position), single-field
`PARAM_DELTA` ≈ 12 B, a full `PARAM_REPORT` fragment ≈ 126 B (**~215 ms on air** at
SF7/BW125 — the number that sets the config ARQ timeout, §11.5). A full set over 158 fields (§8.3), serialising
only VFO slots up to `active_vfos` (FR-8.10), runs to several hundred bytes and
**3–4 fragments, on the order of 1–1.5 s**.

### Appendix C — Defaults

| Key | Default |
|-----|---------|
| `stream.max_rate_hz` | 5 |
| `arq.timeout_ms` | 1000 |
| `arq.max_attempts` | 5 |
| `health.staleness_threshold_s` | 1.0 |
| `health.orange_rate_fraction` | 0.5 |
| `health.rate_window_s` | 10 |
| `bearing.position_epsilon_dm` | 50 |
| `bearing.max_age_ms` | 5000 |
| `kraken.param_apply_timeout_s` | 5 |
| `link.lbt` | 0 |

Radio parameters (`link.channel`, `link.sf`, `link.bw`, `link.cr`, `link.power`)
have **no defaults asserted here** — they are operator-owned (§1.2, FR-20.3).

---

## Related

- [[lora-dtu-sx1262]] — DTU behaviour and AT reference
- [[krakensdr-wiki/README]] — vendored upstream KrakenSDR documentation
- [[deployment]] — two-target sparse-checkout deployment
