# KrakenSim Functional Specification (FSD)

## 0. Purpose & Scope
Provide a deterministic ESP32-based simulator that mimics two KrakenSDR receivers by serving Kraken-style direction-of-arrival (DoA) CSV messages over HTTP. Downstream tooling should be able to swap between these simulated devices and real hardware by only changing the device IP/ports. The moving target path is independent of station placement.

## 1. Objectives and Non-Goals
**Objectives**
- Host two independent HTTP listeners (Station A and Station B) that refresh a Kraken CSV payload once per update tick.
- Expose a lightweight JSON status endpoint for each station with the latest bearing and RSSI metadata.
- Keep the simulator deterministic unless jitter knobs are enabled.

**Non-Goals**
- Streaming over UDP or MQTT.
- Implementing the full Kraken API surface (only `/`, `/DOA_value.html`, `/status.json` are provided).
- Providing UI, TLS termination, or authentication.

## 2. High-Level Behavior
- A single moving object travels linearly from `OBJ_START_(LAT/LON)` to `OBJ_END_(LAT/LON)`; the path is sampled every `BURST_PERIOD_S` seconds (default 1 Hz).
- Each tick recomputes the bearing, width, RSSI, and spectrum for both stations and caches the formatted CSV line (`lastCsvA`, `lastCsvB`).
- HTTP handlers read the cached payloads; no per-request recomputation occurs.
- End-of-path handling obeys `ON_REACH_END`: stop, hold, or loop to the start.

## 3. External Interfaces
- **Transport:** Two HTTP/1.1 servers bound to `HTTP_PORT_A` and `HTTP_PORT_B` on `WiFi.localIP()`.
- **Routes:**
  - `/` -> plaintext help string.
  - `/DOA_value.html` -> latest Kraken CSV line (`text/html` response, but body is CSV).
  - `/status.json` -> JSON snapshot `{id, lat, lon, bearing, rssi}` for quick diagnostics.
- **CSV field order:** `timestamp_ms`, `bearing_deg`, `confidence_pct`, `rssi_dbfs`, `center_freq_hz`, `array_type`, `latency_ms`, `station_id`, `station_lat`, `station_lon`, `gps_heading_deg`, `compass_heading_deg`, `gps_source`, four reserved zeros, followed by `N_BINS` spectrum amplitudes.
- **Formatting:** bearing is emitted as an integer; confidence uses one decimal place; RSSI uses `RSSI_DECIMALS`; latitude/longitude use `LATLON_DECIMALS`; spectrum bins use `SPEC_DECIMALS`.
- **Client example:** `curl http://192.168.0.175:8081/DOA_value.html` (Station A), `curl http://192.168.0.175:8082/status.json` (Station B).

## 4. Configuration & Inputs
- Credentials supplied via `include/credentials.h` (`WIFI_SSID`, `WIFI_PASS`).
- Station macros in `config.h`: `STATION_ID_*`, `STATION_LAT_*`, `STATION_LON_*`, `STATION_ALT_*`, `HTTP_PORT_*`.
- Path and motion: `OBJ_START_*`, `OBJ_END_*`, `SPEED_MPS`, `BURST_PERIOD_S`, `BURST_JITTER_MS`, `ON_REACH_END`.
- Signal/spectrum: `CENTER_FREQ_HZ`, `ARRAY_TYPE`, `N_BINS`, `BACKGROUND_LEVEL`, plus model tuning (`BASE_WIDTH_RAD`, `K_WIDTH_RAD_PER_M`, `RSSI_REF_DB_AT_1M`, `RSSI_NOISE_DB`, `PEAK_SCALE_DIV`).
- Formatting knobs: `BEARING_DECIMALS`, `WIDTH_DECIMALS`, `RSSI_DECIMALS`, `LATLON_DECIMALS`, `SPEC_DECIMALS`.

## 5. Algorithms
- Motion via linear interpolation of start/end lat/lon with fraction `u = clamp(elapsed / travelTime)`.
- Distance calculated with the haversine formula; bearing uses the great-circle initial bearing equation.
- Width model: `BASE_WIDTH_RAD + K_WIDTH_RAD_PER_M * distance_m`.
- RSSI model: free-space path loss with bounded noise, clamped to [-120, -10] dBFS.
- Spectrum: `N_BINS` Gaussian bump centered at the compass bearing, mapped to the unit circle (90° offset) with background jitter.
- CSV builder outputs timestamp, integer bearing, confidence, RSSI, frequency, array metadata, headings, channel flags, and spectrum bins with configured precision.

## 6. Process & Timing
- `setup()` connects Wi-Fi, computes path metrics, and registers HTTP handlers before starting both servers.
- `loop()` services HTTP clients, then on each scheduled tick updates the cached CSV strings for both stations. Optional jitter perturbs the next tick time.
- When the object reaches its destination, the behavior follows the configured mode (stop/hold/loop).

## 7. Logging & Diagnostics
- Serial output reports Wi-Fi connection status, HTTP port bindings, and endpoint URLs.
- `/status.json` can be polled to monitor bearings and RSSI without parsing the full CSV.
