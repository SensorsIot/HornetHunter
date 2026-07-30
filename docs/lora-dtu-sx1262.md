# SX1262 LoRa DTU — Notes & Reference

Working notes for the SX1262 LoRa DTU modules (Waveshare USB-TO-LoRa-xF), as
characterised on the Universal Embedded Workbench. Written for a multi-station
project.

> **Confidence key**
> - ✅ **verified on this hardware** — observed directly on our two sticks
>   (firmware **Ver1.2**, LF variant)
> - 📖 **vendor documentation** — stated by Waveshare; not independently confirmed
> - ⚠️ **inferred / unverified** — needs a measurement before it is relied upon
>
> Sources: [Waveshare USB-TO-LoRa-xF wiki](https://www.waveshare.com/wiki/USB-TO-LoRa-xF),
> the shipped `sscom` presets in [SX1262-LoRa-DTU-sscom5.13.1-en/](SX1262-LoRa-DTU-sscom5.13.1-en/),
> and direct AT probing over the workbench.

> **Correction notice.** An earlier revision of this document concluded that AT
> configuration mode was unreachable over the wire and required a hardware config
> pin. **That was wrong** — the escape sequence must be terminated with CRLF. See
> §3. Several parameter values and ranges were also wrong; the AT table in §7 has
> been rebuilt from live queries.

---

## 1. What these are

- SX1262 (sub-GHz LoRa) modules on a **USB-serial dongle** (WCH CH343,
  `1a86:55d3`, enumerates as `/dev/ttyACM*`). ✅
- Two variants, and they only talk to their own kind: 📖

  | Variant | Band | Default channel |
  |---------|------|-----------------|
  | **LF** (ours) | 410–490 MHz | **23 → 433 MHz** ✅ |
  | HF | 850–930 MHz | 18 → 868 MHz |

- Default personality is a **transparent data pipe** (`AT+MODE=1`, "Stream"): bytes
  into one module's UART come out its peers' UARTs over LoRa. ✅
- Three operating modes: **1 = Stream, 2 = Packet, 3 = Relay** (§6). ✅
- Configured over an AT command set (§7), reachable over the serial link (§3).
- Advertised: 22 dBm max TX, −148 dBm sensitivity (see §5 for what that figure
  actually means), up to 5 km, AES, 960-byte cache, automatic packetisation. 📖

### Channel-to-frequency mapping ✅

Channels are `0..80` in **1 MHz** steps:

```
LF:  f_MHz = 410 + channel      (channel 23 → 433 MHz)
HF:  f_MHz = 850 + channel      (channel 18 → 868 MHz)
```

Confirmed both ways: our LF sticks default to `TXCH=RXCH=23`, and Waveshare
documents 18 → 868 MHz for HF.

## 2. Reaching them on the workbench

Each dongle is a workbench slot exposed as an **RFC2217** serial-over-TCP port.
Drive them through the workbench (never SSH).

| Slot | RFC2217 URL |
|------|-------------|
| SLOT1 | `rfc2217://workbench.local:4001` |
| SLOT3 | `rfc2217://workbench.local:4003` |

- **Do not hardcode device nodes.** The slot→`/dev/ttyACM*` mapping is
  enumeration-order dependent and has been observed to swap between boots. ✅
  Resolve slot → URL from `GET http://workbench.local:8080/api/devices`.
- `workbench.local` does not resolve inside the devcontainer (no mDNS); the
  container adds a `--add-host` mapping. See `.devcontainer/devcontainer.json`.
- **UART: 115200 8N1** ✅ (wrong baud → garbage both ways).
- Open with **`dtr=False, rts=False`**, matching the workbench's own
  `serial_monitor`; the proxy passes DTR/RTS through. ✅
- One RFC2217 client at a time per port. ✅

## 3. Entering AT mode — the CRLF gotcha

**The escape is `+++\r\n`. A bare `+++` is silently ignored.** ✅

This is the correction referenced at the top. Probed directly on SLOT1:

```
'+++'        -> (no response)
'+++\r\n'    -> '+++'          then AT mode is active
```

Everything follows from that:

- **Every AT command must be CRLF-terminated.** 📖✅ The vendor FAQ says as much:
  *"Enable carriage return and line feed by checking the corresponding option. Use a
  baud rate of 115200 to send '+++' to enter command mode."*
- **There is no config pin or jumper.** The KEY button does only two things: 📖
  - held within 3 s of power-on → firmware update mode
  - held **after** 3 s of power-on → **factory reset** (an accidental-reset hazard
    worth considering for field enclosures)
- **Settings take effect on `AT+EXIT`.** Leaving AT mode is mandatory or the
  parameters do not apply. 📖
- **Always guarantee `AT+EXIT`** on every exit path. A stick left in AT mode passes
  no data. Recovery is `AT+REBOOT` (§7) or a power cycle.
- In AT mode the module **does** answer on its own port — unlike transparent mode.
  The "no local echo" behaviour below applies only to data mode.

### Other transparent-mode gotchas ✅

- **No local echo.** Bytes written to a module are not echoed on that port — they
  cross to the peer. Debugging by writing to a port and reading the *same* port
  shows nothing even when the link is fine. Read the **peer**.
- **First-packet warm-up loss.** After opening a fresh connection the first 1–2
  transmissions are dropped while the link settles; steady state is then 100 %. Send
  a throwaway frame and discard the reply.
- **A DTR/RTS pulse does not reset the module**, and the modules emit no boot
  banner. Use `AT+REBOOT` for a software reset.

## 4. Error correction & reliability

LoRa gives you integrity for free, but **not** delivery.

- **FEC**: built into the LoRa PHY via the coding rate. Ours is `CR=1` = 4/5
  (lightest); configurable to 4/8 for stronger correction at the cost of airtime. ✅
- **CRC**: the SX1262 appends a payload CRC and the receiver validates it in
  hardware. **A failed packet is silently dropped, never delivered**, and **the CRC
  cannot be disabled**. 📖 Every test here was byte-perfect. ✅
- **No ARQ.** There is no `AT+ACK` in the command set. A packet lost to a failed CRC
  is simply gone.

**Therefore: you get loss, never corruption.** If lost packets matter, add a thin
app-layer protocol — and it can be simple, because detection is already guaranteed:
**sequence number + ACK + retransmit** is sufficient, and you never have to detect
corruption yourself. If loss is acceptable, add nothing.

**`AT+LBT=1` costs up to 2 seconds.** Listen-before-talk delays a transmission
while the channel is noisy, *"the maximum delay is two seconds, and the sending will
be forced after more than two seconds."* 📖 Any ARQ timeout must exceed that, or LBT
will manufacture phantom retransmissions. If a higher layer already schedules the
medium, LBT is redundant and harmful — leave it off.

## 5. Performance vs packet length

Measured at SF7 / BW 125 kHz / CR 4-5, byte-perfect at every size. ✅

| payload | latency | throughput |
|--------:|--------:|-----------:|
| 4 B   | 61 ms  | 66 B/s |
| 16 B  | 81 ms  | 198 B/s |
| 32 B  | 102 ms | 314 B/s |
| 64 B  | 162 ms | 396 B/s |
| 128 B | 263 ms | 487 B/s |
| 200 B | 385 ms | 520 B/s |
| 240 B | 445 ms | **540 B/s** |

- **Airtime is independent of link margin.** ✅ These figures were re-measured with
  **dummy loads** fitted instead of antennas and reproduced to within 1 ms. Useful
  consequence: the bench is valid for protocol and timing work — but bench
  *reliability* figures are best-case and say nothing about the field.
- There is a **fixed ~50–60 ms cost per packet** (preamble, header, and
  transparent-mode idle-gap packetisation), paid regardless of size. So a 6-byte
  payload and a 16-byte payload cost almost the same airtime.
- **Throughput climbs ~8×** from tiny to ~240 B packets as that overhead is
  amortised, flattening toward ~540 B/s for this configuration.
- **Rule: batch, don't dribble.** 240 B as one packet = 445 ms; the same data as
  sixty 4-B packets ≈ 3.7 s. Only use small packets for genuinely small,
  latency-sensitive messages — the ~60 ms floor is unavoidable.
- **Maximum single packet is exactly 240 bytes.** 📖 *"the maximum data size of a
  single packet is 240 bytes, if it exceeds 240 bytes, it will be automatically
  packetized."* Keep frames under 240 B and framing stays predictable.
- The 960-byte cache and automatic packetisation are **fixed and not
  configurable**. 📖

### What "−148 dBm" actually means ⚠️

The headline sensitivity is the **best case at maximum spreading factor and minimum
bandwidth** (SF12 / 7.8 kHz), not at our settings. At **SF7 / BW 125 kHz** the
SX1262 datasheet gives roughly **−123 dBm** — about 25 dB worse. Range expectations
and any RSSI threshold must be based on the configured mode, not the headline.
(Datasheet-derived; not measured here.)

Raising SF increases every latency in the table substantially; stronger CR adds a
little; wider BW cuts airtime roughly proportionally at the cost of sensitivity.

## 6. Addressing and modes

### Stream mode (`AT+MODE=1`) — group-addressed, *not* a flat broadcast 📖

This is the correction that matters most for network design. A receiver in Stream
mode accepts a frame only when **both its address and its channel match the
sender's** — with one exception: **address `0xFFFF`** receives from all addresses on
its channel, and its own transmissions reach all of them.

Waveshare's own example table proves it: with Devices A/C/D at `0xFFFE` and Device E
at `0x0000`, all on channel 18, **E does not receive A's traffic**. Device F on
channel 65 receives nothing.

So the useful topology for a master/stations network is:

| Node | `AT+ADDR` | Effect |
|------|-----------|--------|
| Master | `0xFFFF` | its transmissions reach every station; it hears every station |
| Station *n* | `0x0001`, `0x0002`, … | stations are **mutually deaf** |

Mutual deafness is a feature: no station is confused by another's replies, and one
broadcast transmission from the master reaches all stations at once. All nodes must
share `SF`, `BW`, `CR`, `NETID` and channel.

Because the medium is half-duplex with no MAC, something must impose order —
master/polling is the simplest and is collision-free by construction.

### Packet mode (`AT+MODE=2`) — module-level addressing 📖

The **first 3 bytes** of each transmitted stream specify the destination:

```
[ADDR_hi][ADDR_lo][channel] payload…

  e.g.  FF FE 12 AA   → to address 0xFFFE on channel 0x12 (18), payload 0xAA
```

`0xFFFF` is the broadcast address. This costs 3 bytes per frame, versus 1–2 bytes
for app-layer addressing in Stream mode — so Stream mode plus your own header is
both cheaper and simpler unless you specifically want the module to filter.

### Relay mode (`AT+MODE=3`) 📖

Relay nodes forward Stream- or Packet-mode traffic and **output nothing on their own
interface**. Endpoints keep their normal mode and are distinguished by `NETID`; the
relay's address is formed from the endpoint `NETID` values. Multi-level chains are
supported.

## 7. AT command reference

Query `AT+X?`, set `AT+X=value`, and `AT+X=?` reports the parameter's **type** (not
its range). All commands CRLF-terminated. Values below are the **live defaults read
from our LF sticks, firmware Ver1.2**. ✅

| Command | Meaning | Ours | Range / notes |
|---------|---------|------|---------------|
| `+++` | enter AT mode | — | **CRLF required** (§3) |
| `AT+EXIT` | leave AT mode, apply settings | — | mandatory |
| `AT` / `ATE` | ping / toggle echo | — | |
| `AT+VER` | firmware version | `Ver1.2` | |
| `AT+HELP` | list commands | — | authoritative command list |
| `AT+AllP?` | dump all parameters | see below | ⚠️ field order differs from docs |
| `AT+SF` | spreading factor | `7` | `UINT8`, 7–12; higher = more range, slower |
| `AT+BW` | bandwidth | `0` | `UINT16`. **An index: 0 = 125 kHz, 1 = 250, 2 = 500** |
| `AT+CR` | coding rate (FEC) | `1` | `UINT8`, 1–4 → 4/5, 4/6, 4/7, 4/8 |
| `AT+PWR` | TX power, dBm | `10` | `UINT8`, 10–22 |
| `AT+NETID` | network id | `0` | `UINT8` → **0–255** |
| `AT+LBT` | listen-before-talk | `0` | `UINT8`. `1` can delay TX up to 2 s (§4) |
| `AT+MODE` | operating mode | `1` | `UINT8`. 1 Stream, 2 Packet, 3 Relay |
| `AT+TXCH` | TX channel | `23` | `UINT8`, 0–80. LF: 410+n MHz |
| `AT+RXCH` | RX channel | `23` | `UINT8`, 0–80 |
| `AT+RSSI` | append RSSI to received data | `0` | `INT8`. `1` appends a byte **after your payload** |
| `AT+ADDR` | node address | `0` | `UINT16`, 0–65535. `0xFFFF` = broadcast/monitor |
| `AT+PORT` | UART interface type | `1` | `UINT8`. `3` = RS232 on the RS232 DTU; **`1` on the USB stick — leave alone** |
| `AT+COMM` | UART frame format | `"8N1"` | `STRING` |
| `AT+BAUD` | UART baud | `115200` | `UINT32`, 1200–115200 |
| `AT+KEY` | AES key | *(empty)* | `UINT16` **[write-only]** — see below |
| `AT+RESTORE` | factory reset | — | `UINT8` **[write-only]** |
| `AT+REBOOT` | software reboot | — | ✅ **undocumented by the vendor**; useful, since DTR/RTS does not reset |

### `AT+KEY` is write-only, and is not security ✅

`AT+KEY=?` reports `<ENCRYPT KEY:UINT16[WO]>`, and `AT+KEY?` returns an empty value.
So the key **cannot be read back or audited** — a push-and-verify configuration flow
cannot confirm it.

More importantly: the device does offer AES, but keyed by a **16-bit** value —
roughly 65 000 possibilities. Treat `AT+KEY` as a **network separator**, never as
confidentiality.

### `AT+AllP?` — do not parse positionally ⚠️

Live response from our sticks:

```
+ALLP=7,0,1,10,0,0,1,23,23,0,0,1,"8N1",115200,0
      SF BW CR PWR NETID LBT MODE TXCH RXCH RSSI ADDR PORT COMM BAUD KEY
```

Waveshare documents the same command with **`BAUD` and `COMM` in the opposite
order**, and with `BW` as `125` rather than as an index. Query parameters
individually rather than relying on this string.

## 8. Open items

- Whether `AT+BW`'s index form is universal or firmware-version dependent — the
  shipped `sscom` presets use `AT+BW=125`, which contradicts Ver1.2. ⚠️
- Actual sensitivity at SF7/BW125 on this hardware (datasheet says ~−123 dBm). ⚠️
- Behaviour when a logical frame exceeds 240 B and is auto-packetised **with
  `AT+RSSI=1` enabled** — whether one RSSI byte is appended per LoRa packet or per
  logical write. Avoided entirely by keeping frames under 240 B. ⚠️
- Whether `AT+RESTORE=1` also clears the AES key. ⚠️

---

## Appendix A — Python test / utility script

Standalone; needs only `pyserial`. Talks to the DTUs through the workbench RFC2217
ports. Run with no args for a link + performance demo.

```python
#!/usr/bin/env python3
"""SX1262 LoRa DTU link + performance tester (via workbench RFC2217).

Two DTUs in transparent mode: bytes written to one UART come out the other.
Usage:  python3 lora_dtu.py [host] [portA] [portB]
        (defaults: workbench.local 4001 4003)
"""
import sys, time, serial

HOST  = sys.argv[1] if len(sys.argv) > 1 else "workbench.local"
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

## Appendix B — Read-only AT probe

Enters AT mode, dumps every parameter, and always exits. Issues **queries only** —
no setters — so it is safe to run against configured hardware.

```python
#!/usr/bin/env python3
"""Read-only AT probe of a LoRa DTU via the workbench RFC2217 port."""
import sys, time, serial

HOST = "workbench.local"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4001
QUERIES = ["AT+VER", "AT+AllP?", "AT+SF?", "AT+BW?", "AT+CR?", "AT+PWR?",
           "AT+NETID?", "AT+LBT?", "AT+MODE?", "AT+TXCH?", "AT+RXCH?",
           "AT+RSSI?", "AT+ADDR?", "AT+PORT?", "AT+BAUD?", "AT+COMM?", "AT+KEY?"]


def drain(s, quiet=0.35, hard=3.0):
    end, buf, last = time.time() + hard, b"", time.time()
    while time.time() < end:
        if s.in_waiting:
            buf += s.read(s.in_waiting); last = time.time()
        elif buf and time.time() - last > quiet:
            break
        else:
            time.sleep(0.02)
    return buf


def clean(raw):
    parts = raw.decode("utf-8", "replace").replace("\r", "").split("\n")
    return " | ".join(p for p in parts if p.strip())


s = serial.serial_for_url(f"rfc2217://{HOST}:{PORT}", do_not_open=True)
s.baudrate, s.timeout, s.dtr, s.rts = 115200, 0.2, False, False
s.open()
time.sleep(0.6)
try:
    s.reset_input_buffer()
    s.write(b"+++\r\n"); s.flush()          # CRLF is required -- see section 3
    if not drain(s).strip():
        print(f"port {PORT}: could not enter AT mode"); raise SystemExit(1)
    for q in QUERIES:
        s.reset_input_buffer()
        s.write(q.encode() + b"\r\n"); s.flush()
        print(f"  {q:<12} {clean(drain(s))}")
finally:
    s.write(b"AT+EXIT\r\n"); s.flush(); drain(s)   # never leave it in AT mode
    s.close()
```

---

## Related

- [[hornethunter-fsd]] — how this link is used: framing, ARQ, addressing
- [[krakensdr-integration]] — the other half of the station's interfaces
