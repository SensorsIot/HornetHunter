# SX1262 LoRa DTU — Notes & Reference

Working notes for the SX1262 LoRa DTU modules (transparent serial-over-LoRa
bridges), as characterised on the Universal Embedded Workbench. Written for a
multi-station project (≥ 4 stations).

> **Confidence key:** ✅ verified on hardware · ⚠️ inferred / needs the vendor
> manual to confirm. AT-register introspection was **not** possible remotely
> (see §3), so anything about AT values below the wire is from the shipped
> `sscom` presets, not a live read.

---

## 1. What these are

- SX1262 (sub-GHz LoRa) modules on a **USB-serial dongle** (WCH CH343,
  `1a86:55d3`, enumerates as `/dev/ttyACM*`).
- Default personality is a **transparent data pipe**: bytes into one module's
  UART come out the peer's UART over LoRa. No framing, addressing, or
  handshake of your own is required for a 2-node link.
- Configured over an **AT command set** (§7). Three shipped example modes:
  Packet, Stream, Relay (the `sscom` preset folders) — they differ mainly in
  `AT+KEY` (encryption) and the relay/repeater role. ⚠️ Not fully characterised.

## 2. Reaching them on the workbench

Each dongle is a workbench slot exposed as an **RFC2217** serial-over-TCP port.
Drive them through the workbench (never SSH):

| Slot | RFC2217 URL | devnode |
|------|-------------|---------|
| SLOT1 | `rfc2217://<host>:4001` | `/dev/ttyACM1` |
| SLOT3 | `rfc2217://<host>:4003` | `/dev/ttyACM0` |

- **UART: 115200 8N1** ✅ (wrong baud → garbage both ways; 115200 gives clean
  byte-exact transfer).
- Open with **`dtr=False, rts=False`** (matches the workbench's own
  `serial_monitor`; the proxy passes DTR/RTS through).
- The RFC2217 proxy honours the client's negotiated baud (pyserial
  `PortManager`), so `serial.serial_for_url("rfc2217://…")` with `baudrate=…`
  works as expected. See the script in Appendix A.

## 3. How the link works — and the gotchas that cost time

- **Transparent mode has no local echo.** Bytes you write to a module are *not*
  echoed back on that same port — they cross to the peer. Debugging by writing
  `AT`/`+++` to a port and reading the *same* port shows nothing even though the
  link is fine. Always read the **peer** port. ✅ (this was the single biggest
  red herring.)
- **First-packet warm-up loss.** ✅ Right after opening a fresh connection the
  first 1–2 transmissions are dropped while the link settles; steady-state is
  then 100 %. Send a throwaway byte and discard the first reply.
- **AT mode could not be entered over the wire.** ⚠️ `+++` (the escape, per the
  preset `N2=A,+++`) with a proper ≥1 s guard produced no response at any baud
  or DTR/RTS state. On this DTU AT/config mode is most likely gated behind a
  **hardware config pin/button**, not reachable through the CH343 control lines.
  Plan to configure the modules **before** deployment (with the vendor tool /
  the config pin), not remotely through the workbench.
- **A DTR/RTS pulse does not reset the module** (`/api/serial/reset` captured no
  boot banner), and the modules emit **no boot output**. ✅

## 4. Error correction & reliability

LoRa gives you integrity for free, but **not** delivery:

- **FEC (forward error correction):** built into the LoRa PHY via the coding
  rate. Preset is `CR=1` = **4/5** (lightest). Configurable to 4/8 for stronger
  correction at the cost of airtime.
- **CRC (error *detection*):** the SX1262 appends a payload CRC and the receiver
  validates it in hardware — **a failed packet is silently dropped, never
  delivered.** ✅ Every test here was byte-perfect; corrupt bytes are never
  handed up. (Contrast the OOK 433 sensors, whose weak 8-bit CRC lets corruption
  through — **this link needs no plausibility/sanity filter.**)
- **No ARQ (no acknowledgement / retransmission).** The command set has no
  `AT+ACK`. A packet lost to a failed CRC is simply gone → you get **loss, never
  corruption.**

**Implication:** if lost packets matter, add a *thin* app-layer reliability
protocol — and it can be simple because detection is already guaranteed:
**sequence number + ACK + retransmit** is enough; you never have to detect
corruption yourself. If loss is acceptable (periodic telemetry), add nothing.

## 5. Performance vs packet length

Measured at SF7 / BW125 / CR4-5, byte-perfect at every size:

| payload | latency | throughput |
|--------:|--------:|-----------:|
| 4 B   | 61 ms  | 66 B/s |
| 16 B  | 81 ms  | 198 B/s |
| 32 B  | 102 ms | 314 B/s |
| 64 B  | 162 ms | 396 B/s |
| 128 B | 263 ms | 487 B/s |
| 200 B | 385 ms | 520 B/s |
| 240 B | 445 ms | **540 B/s** |

- There is a **fixed ~50–60 ms cost per packet** (preamble + header +
  transparent-mode idle-gap packetisation), paid regardless of size.
- So **throughput climbs ~8×** from tiny to ~240 B packets as that overhead is
  amortised, flattening toward ~540 B/s (the channel limit for this config).
  ~240 B is a single LoRa frame; larger writes fragment. ⚠️ (fragment threshold
  not pinned down; ≥ ~240 B).
- **Rule: batch, don't dribble.** 240 B as one packet = 445 ms; the same data as
  sixty 4-B packets ≈ 3.7 s. Only use small packets for genuinely small,
  latency-sensitive messages (the ~60 ms floor is unavoidable).
- These numbers are **config-specific**: raising SF (for range) increases every
  latency substantially; stronger CR adds a little.

## 6. Addressing multiple stations (≥ 4)

Our 2-node test used the simplest case: both modules identical (`NETID=0`,
`TXCH=RXCH=18`, `ADDR=0`, transparent) → a **broadcast bus**. Two ways to scale
to 4+ stations:

### Option A — Transparent broadcast bus + app-layer addressing ✅ (proven base)

- Give every station **the same** radio params: `SF`, `BW`, `CR`, `NETID`, and
  `TXCH=RXCH` (one channel).
- Every station then hears **every** transmission. You add addressing **in your
  payload** — e.g. a small header `[dest_id][src_id][seq]…` — and each station
  ignores frames not addressed to it.
- It is a **half-duplex shared medium with no built-in MAC**: only one station
  may transmit at a time and there is no collision avoidance unless you enable
  **`AT+LBT=1`** (listen-before-talk). With 4+ nodes you *must* impose order:
  - **Master/polling (recommended):** one coordinator polls each station by id
    in turn; a station transmits only when polled. Deterministic, collision-free,
    easy to reason about. Pair with seq+ACK per exchange (§4).
  - or lightweight TDMA / random-backoff + LBT if there is no natural master.

### Option B — Module-level fixed-point addressing ⚠️ (needs manual)

- The AT set exposes `AT+ADDR` (node address), `AT+NETID` (network), and
  `AT+TXCH`/`AT+RXCH` (channels) — the ingredients for a "fixed transmission"
  mode where each frame is prefixed with a **target address (+channel)** and the
  module delivers only to that node.
- This offloads addressing to the module, but the **exact frame prefix format
  and the `AT+MODE` value that enables it are not confirmed** (could not enter
  AT mode; no manual on hand). Verify from the vendor manual before relying on it.

### Recommended architecture for the 4-station project

1. All stations on **one `NETID` + one channel + identical SF/BW/CR** (use a
   *different* `NETID` only to isolate a separate network).
2. **Master-poll** on that shared channel with a 1-byte station id in the
   payload header (Option A). Deterministic, no collisions, scales past 4.
3. Add **seq + ACK + retransmit** for any data that must not be lost (§4).
4. Keep payloads **chunky** (near ~240 B) for throughput (§5); one poll→reply
   round trip is ~0.9 s at 240 B each way, so budget the poll cycle accordingly
   (4 stations ≈ a few seconds per full round at max payload).
5. Enable **`AT+LBT=1`** as a safety net against overlap.
6. **Configure every module before deployment** (§3) — addresses/params can't be
   set remotely through the workbench.

## 7. AT command reference

Query form `AT+X?`, set form `AT+X=value`. Values shown are the shipped preset.
Enter AT mode with `+++` (guard-timed); leave with `AT+EXIT`. ⚠️ Remote entry
did not work here — assume a config pin is required.

| Command | Meaning | Preset / values |
|---------|---------|-----------------|
| `AT` | ping (expect `OK`) | — |
| `+++` | escape data→AT mode (no CR, ~1 s guard) | — |
| `ATE` | toggle command echo | — |
| `AT+VER` | firmware version | — |
| `AT+HELP` | list commands | — |
| `AT+AllP` | dump all parameters | — |
| `AT+EXIT` | leave AT → transparent/data mode | — |
| `AT+SF` | spreading factor | `7` (range 7–12; higher = more range, slower) |
| `AT+BW` | bandwidth (kHz) | `125` (125 / 250 / 500) |
| `AT+CR` | coding rate (FEC) | `1` → 4/5 (1–4 → 4/5…4/8) |
| `AT+PWR` | TX power (dBm) | `22` |
| `AT+NETID` | network id (network separator) | `0` |
| `AT+ADDR` | this node's address | `0` |
| `AT+TXCH` | TX channel index | `18` |
| `AT+RXCH` | RX channel index | `18` |
| `AT+MODE` | operating mode | `1` ⚠️ (transparent vs fixed-point mapping unconfirmed) |
| `AT+LBT` | listen-before-talk | `0` (set `1` for multi-node) |
| `AT+RSSI` | append RSSI to received data | `0` |
| `AT+KEY` | encryption on/off | `1` packet mode / `0` stream mode |
| `AT+PORT` | vendor "port" grouping | `3` ⚠️ |
| `AT+COMM` | UART frame format | `"8N1"` |
| `AT+BAUD` | UART baud | `115200` |

For a point-to-point pair (our test): identical `SF/BW/CR/NETID`, `TXCH=RXCH`,
`AT+EXIT` into transparent mode → type on one, read on the other.

---

## Appendix A — Python test / utility script

Standalone; needs only `pyserial`. Talks to the DTUs through the workbench
RFC2217 ports. Run with no args for a link + performance demo.

```python
#!/usr/bin/env python3
"""SX1262 LoRa DTU link + performance tester (via workbench RFC2217).

Two DTUs in transparent mode: bytes written to one UART come out the other.
Usage:  python3 lora_dtu.py [host] [portA] [portB]
        (defaults: 192.168.0.87 4001 4003)
"""
import sys, time, serial

HOST  = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.87"
PORTA = int(sys.argv[2]) if len(sys.argv) > 2 else 4001
PORTB = int(sys.argv[3]) if len(sys.argv) > 3 else 4003
BAUD  = 115200


def open_dtu(port, baud=BAUD):
    s = serial.serial_for_url(f"rfc2217://{HOST}:{port}", do_not_open=True)
    s.baudrate = baud
    s.timeout = 0.1
    s.dtr = False           # match the workbench serial_monitor
    s.rts = False
    s.open()
    return s


def recv(s, need=1, quiet=0.3, hard=5.0):
    """Read until `need` bytes AND a trailing newline, or timeout."""
    end = time.time() + hard
    buf = b""
    while time.time() < end:
        n = s.in_waiting
        if n:
            buf += s.read(n)
            end = time.time() + quiet
        else:
            time.sleep(0.02)
        if buf.endswith(b"\n") and len(buf) >= need:
            break
    return buf


def warmup(a, b):
    """Absorb the first-packet loss on a fresh link."""
    for tx, rx in ((a, b), (b, a)):
        rx.reset_input_buffer()
        tx.write(b"warmup\n"); tx.flush()
        recv(rx, 2, hard=2.0)
    time.sleep(0.3)


def link_test(a, b, n=6):
    print("== bidirectional link ==")
    ok = 0
    for i in range(n):
        tx, rx, s, d = (a, b, "A", "B") if i % 2 == 0 else (b, a, "B", "A")
        rx.reset_input_buffer()
        msg = f"m{i:02d} {s}>{d} payload-abcdefghijklmnop\n".encode()
        t0 = time.time(); tx.write(msg); tx.flush()
        got = recv(rx, len(msg)); dt = (time.time() - t0) * 1000
        good = got.strip() == msg.strip(); ok += good
        print(f"  {s}->{d} {'OK ' if good else 'LOST'} {dt:4.0f}ms")
        time.sleep(0.25)
    print(f"  {ok}/{n} intact\n")


def perf_sweep(a, b, sizes=(4, 16, 32, 64, 128, 200, 240)):
    print("== performance vs payload size (A->B) ==")
    print(f"  {'size':>5} {'lat_ms':>7} {'B/s':>7}  integrity")
    for size in sizes:
        p = b"S" + str(size).encode() + b":"
        p = p + b"." * (size - len(p) - 1) + b"\n"
        lats, good = [], True
        for _ in range(3):
            b.reset_input_buffer()
            t0 = time.time(); a.write(p); a.flush()
            got = recv(b, size); dt = (time.time() - t0) * 1000
            if got.strip() == p.strip():
                lats.append(dt)
            else:
                good = False
            time.sleep(0.3)
        if lats:
            avg = sum(lats) / len(lats)
            print(f"  {size:>5} {avg:>7.0f} {size/(avg/1000):>7.0f}  {'OK' if good else 'LOSS'}")
        else:
            print(f"  {size:>5} {'--':>7} {'--':>7}  ALL LOST")


if __name__ == "__main__":
    a = open_dtu(PORTA); b = open_dtu(PORTB)
    time.sleep(0.5)
    try:
        warmup(a, b)
        link_test(a, b)
        perf_sweep(a, b)
    finally:
        a.close(); b.close()
```
