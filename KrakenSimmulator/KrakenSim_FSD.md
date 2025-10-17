# KrakenSim Functional Specification (FSD)

## 0. Purpose & Scope
Deliver a deterministic simulator that hosts two independent KrakenSDR-style direction-of-arrival (DoA) HTTP endpoints on separate ports. Downstream software can talk to the simulated devices exactly as it would to real Kraken units by issuing HTTP requests against each port. The moving target path remains independent of station placement, and only the server port configuration needs to change when swapping between simulated and real hardware.

## 1. Objectives and Non-Goals
**Objectives**
- Serve two distinct HTTP listeners, each representing a KrakenSDR DoA endpoint.
- Answer DoA requests with JSON payloads that mirror real device fields (timestamp, bearing, width, RSSI, frequency, array type, sequence ID, station metadata, spectrum, etc.).
- Update the simulated measurement state at a fixed cadence so repeated requests observe evolving bearings as the object traverses from `OBJ_START` to `OBJ_END`.
- Remain deterministic unless randomness knobs are enabled (seedable jitter).

**Non-Goals**
- UDP streaming or CSV payloads (transport is HTTP only in v1).
- Exhaustive emulation of every Kraken firmware API route (focus on DoA essentials).
- UI or visualization layers.

## 2. High-Level Behavior
- Two logical devices (Station A and Station B) live inside one process.
- A single object travels linearly from `OBJ_START_(LAT/LON)` to `OBJ_END_(LAT/LON)` over `DURATION_S`.
- At `RATE_HZ`, the simulator recomputes distance, bearing, width, RSSI, and spectrum for each station and caches the latest sample.
- Each HTTP request returns the most recent sample for the addressed station, optionally exposing a short history buffer if requested.

## 3. External Interfaces
### 3.1 Transport
- HTTP/1.1 servers bound to `BIND_HOST:PORT_A` and `BIND_HOST:PORT_B`.
- Default host: `127.0.0.1`. Default ports: `8001` (Station A), `8002` (Station B).
- No TLS or authentication in v1; reverse proxies can be layered externally if needed.

### 3.2 DoA Endpoint
- `GET /api/v1/doa`: returns the most recent measurement as JSON.
- Optional query parameters:
  - `history=<n>`: return the last `n` samples (bounded by `MAX_HISTORY`, default 10).
  - `format=csv`: when present, return CSV text matching Kraken field order for compatibility layers that still expect CSV over HTTP.
- Response headers: `Content-Type: application/json` (default) or `text/csv` when requested.
- JSON schema (single sample):
  ```json
  {
    "timestamp_ms": 1713206400123,
    "sequence": 42,
    "station_id": "FAKE1",
    "station": {
      "lat": 47.474242,
      "lon": 7.765962,
      "alt_m": 400.0
    },
    "bearing_deg": 123.45,
    "width_rad": 0.213456,
    "rssi_dbfs": -47.12,
    "center_freq_hz": 148524000,
    "array_type": "ULA",
    "speed_mps": 6.0,
    "gps_source": "GPS",
    "channel_flags": ["R", "R", "R", "R"],
    "spectrum": [0.08, 0.10, 0.12, "..."]
  }
  ```
- For history responses, wrap samples in a JSON array ordered newest first.

### 3.3 Metadata Endpoint (optional)
- `GET /api/v1/metadata`: returns station constants (IDs, coordinates, update rate, bin count, jitter flags) to help clients bootstrap.

### 3.4 Health Endpoint
- `GET /healthz`: returns `200 OK` with body `ok` once the simulator is ready.

## 4. Configuration
All inputs reside in a single constants block.

**Network**: `BIND_HOST`, `PORT_A`, `PORT_B`, `MAX_CONNECTIONS`, `MAX_HISTORY`.

**Station Identity & Position**: `ID_A/B`, `LAT_A/B`, `LON_A/B`, `ALT_A/B`. When `LAT_B/LON_B` are `None`, derive Station B as `SEP_M` meters east of Station A; `ALT_B` still applies.

**Moving Object Path**: `OBJ_START_LAT/LON`, `OBJ_END_LAT/LON`.

**Timing**: `RATE_HZ` (updates per second per station), `DURATION_S` (seconds).

**Signal & Array**: `CENTER_FREQ`, `ARRAY_TYPE`.

**Spectrum**: `N_BINS` (e.g., 181 or 360 to match client expectation), `BACKGROUND_LEVEL`.

**Models**: `BASE_WIDTH`, `K_WIDTH`, `RSSI_REF_DB_AT_1M`, `RSSI_NOISE_DB`, `PEAK_SCALE_DIV`.

**Realism Tweaks**: `POSITION_JITTER_M`, `TIMESTAMP_JITTER_MS`, `ENABLE_RANDOM_SEED`, `SEED_VALUE`.

## 5. Algorithms
### 5.1 Object Motion
Linear interpolation in latitude and longitude with optional jitter. Let `u = clamp(elapsed_s / DURATION_S, 0..1)`; position = `start + u * (end - start)`.

### 5.2 Bearing
Great-circle initial bearing:
```
lat1, lon1, lat2, lon2 in radians
dlon = lon2 - lon1
y = sin(dlon) * cos(lat2)
x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
bearing_deg = fmod((atan2(y, x) * 180 / pi) + 360, 360)
```

### 5.3 Distance
Meters via the haversine formula.

### 5.4 Width Model
`width = BASE_WIDTH + K_WIDTH * distance_m` (clamp if desired).

### 5.5 RSSI Model
```
dist_eff = max(distance_m, 1.0)
noise = uniform(-RSSI_NOISE_DB, +RSSI_NOISE_DB)
rssi = RSSI_REF_DB_AT_1M - 20 * log10(dist_eff) + noise
rssi = clamp(rssi, -120, -10)
```

### 5.6 Spectrum Model
- `N_BINS` evenly spaced bearings over 0–360°. For bin center θ:
  - `d = min(|θ - bearing|, 360 - |θ - bearing|)`
  - `sigma_deg = max(0.5, (width_rad * 180 / pi) / 2)`
  - `peak_amp = max(0.1, (-rssi / PEAK_SCALE_DIV))`
  - `amp(θ) = peak_amp * exp(-0.5 * (d / sigma_deg)^2) + BACKGROUND_LEVEL * uniform(0.8, 1.2)`
- Round to two decimals before returning.

## 6. Process Model
- Single process with a scheduler thread (or async task) that recomputes samples at `RATE_HZ`.
- Two HTTP listeners share the cached sample set; handlers serialize the latest (or requested history) on demand.
- Graceful shutdown closes listeners, flushes logs, and stops the scheduler.

## 7. Logging & Diagnostics
- On start: log station info, object start/end, update rate, bin count, bound ports.
- Health endpoint reports readiness once the first sample is computed.
- Optional verbose mode logs `(seq, bearing, width, rssi)` per update (disabled by default).

## 8. Replaceability Constraints
- JSON keys and numeric formatting mirror the real Kraken DoA API (2 decimals for bearing/RSSI, 6 for lat/lon).
- Update cadence remains within 5–10 Hz by default.
- Swapping to real hardware requires only pointing clients at the actual Kraken host/port.

## 9. Configuration Examples
**Local twin endpoints**: bind `127.0.0.1`, `PORT_A=8001`, `PORT_B=8002`. Client hits `http://127.0.0.1:8001/api/v1/doa` for Station A and `http://127.0.0.1:8002/api/v1/doa` for Station B.

**History query**: `curl "http://localhost:8001/api/v1/doa?history=5"` returns the five most recent samples for Station A.

**CSV compatibility**: `curl "http://localhost:8002/api/v1/doa?format=csv"` responds with a single CSV line in the classic Kraken field order.

## 10. Error Handling
- If an HTTP listener fails to bind, log the error and exit non-zero.
- Validate configuration: `N_BINS >= 3`, `RATE_HZ > 0`, `PORT_A != PORT_B`, `PORT_* > 0`.
- If a computed field is NaN or Infinity, skip publishing that sample, retain the previous valid value, and log a warning.

## 11. Performance Constraints
- Designed to run on low-end systems (Raspberry Pi, small VM).
- Default load (5 Hz updates, JSON serialization on demand) is negligible.
- History buffers sized to avoid unbounded memory growth.

## 12. Security & Networking
- Plain HTTP only; recommend running behind a reverse proxy when exposed beyond localhost.
- No authentication in v1; optional Basic Auth or token support can be added later.
- Rate-limit requests per client (default soft limit: 50 requests/sec) to avoid abuse.

## 13. Extensibility
- Add WebSocket or Server-Sent Events feeds for push-based clients.
- Support TLS termination and auth middlewares.
- Provide replay mode that serves historic captures instead of synthetic motion.
- Emit Prometheus metrics from the scheduler (update latency, request counts).

## 14. Acceptance Criteria
- Both HTTP ports serve healthy responses (`/healthz`) after startup.
- `GET /api/v1/doa` returns valid JSON with all required fields and correct numeric formatting.
- Bearings evolve smoothly along the path; width and RSSI follow their models.
- Spectrum arrays contain exactly `N_BINS` floats.
- History queries honor bounds and order.

## 15. Minimal File Layout
- `krakensim_server.py` (entry point hosting the HTTP listeners and scheduler).
- `models.py` (geometry, RSSI, spectrum helpers).
- `config.py` (constants and overrides).
- Optional `README.md` with quick-start instructions.

## 16. Quick Start
1. Edit constants:
   - Set `PORT_A`, `PORT_B`, and optional `BIND_HOST`.
   - Set `LAT_A/LON_A`, `LAT_B/LON_B` (or `SEP_M`).
   - Set `OBJ_START_*`, `OBJ_END_*`.
2. Run `python3 krakensim_server.py`.
3. Verify health: `curl http://localhost:8001/healthz`.
4. Fetch DoA data:
   - `curl http://localhost:8001/api/v1/doa` (Station A).
   - `curl http://localhost:8002/api/v1/doa` (Station B).
5. Point downstream software at the same URLs to exercise the simulated devices.
