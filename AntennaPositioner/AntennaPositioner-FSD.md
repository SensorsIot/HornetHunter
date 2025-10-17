# Antenna Positioner FSD

## 1. Project Overview
The Antenna Positioner is an ESP32-S3–based controller that measures its heading relative to geographic north, tracks its own position, and exposes that data over Wi-Fi. The device must present both real-time telemetry and a simple control/diagnostic surface for companion tools. Firmware is built with PlatformIO using the Arduino framework and must support OTA updates to avoid physical access during field deployments.

## 2. Goals & Success Metrics
- Continuously estimate yaw/pitch/roll and absolute heading with ±2° accuracy while reporting GPS-derived position (lat/lon/alt) within standard GNSS tolerances.
- Provide Wi-Fi connectivity that serves JSON REST endpoints and an optional WebSocket stream with ≤500 ms latency between sensor capture and network publication.
- Deliver OTA updates over HTTPS (preferred) or authenticated HTTP, completing within 2 minutes on a standard 802.11n link.
- Achieve ≥95% uptime during 24-hour bench tests without watchdog resets.

## 3. Out-of-Scope
- Mechanical actuation (motors, servos) control logic.
- Cloud-side data storage or dashboards.
- Cellular or LoRa backhaul.

## 4. Hardware & Interfaces
- **MCU**: ESP32-S3 Super Mini (Wi-Fi + BLE).
- **Orientation sensor**: 9-DoF IMU with magnetometer (e.g., BNO055 or ICM-20948 + QMC5883L) on I²C.
- **Position sensor**: GNSS receiver (UART) capable of NMEA sentences.
- **Status indicators**: Two LEDs (power, Wi-Fi/OTA) and one momentary button for provisioning/reset.
- **Power**: 5V USB-C with optional LiPo + charger module (telemetry only).

## 5. Software Architecture
- `src/main.cpp` bootstraps PlatformIO app, starts event loop, and initializes subsystems.
- Submodules:
  - `sensors`: drivers, calibration routines, sensor fusion (Madgwick or Mahony filter).
  - `network`: Wi-Fi manager, captive portal fallback, MDNS advertisement.
  - `services`: REST/WebSocket handlers and OTA endpoints.
  - `storage`: preferences (NVS) for Wi-Fi credentials, calibration constants, OTA auth token.
- FreeRTOS tasks:
  - Sensor Task (100 Hz) for IMU acquisition and fusion.
  - GNSS Task (5 Hz) for parsing NMEA sentences.
  - Telemetry Task (5–10 Hz) pushing latest state to queues.
  - Web Service Task handling HTTP/WS requests.

## 6. Functional Requirements
1. **Startup & Provisioning**
   - On first boot, enter AP mode (`AntennaPositioner-XXXX`) and serve a captive portal at `192.168.4.1` for Wi-Fi credential input.
   - Persist credentials securely (NVS) and reboot into station mode.
2. **Sensor Acquisition**
   - Calibrate magnetometer using stored offsets; provide API endpoint to trigger recalibration.
   - Fuse accelerometer and gyroscope with magnetometer data to compute heading relative to true north (optionally adjust using GNSS course over ground).
   - Validate sensor health and expose self-test status.
3. **Position Tracking**
   - Parse GPS data for latitude, longitude, altitude, speed, and UTC time.
   - Maintain last fix timestamp; mark data stale if no fix within 5 seconds.
4. **Telemetry Service**
   - Serve `GET /api/v1/status` returning JSON with heading (deg), orientation quaternion, lat/lon, altitude, velocity, fix age, and firmware metadata.
   - Offer `GET /api/v1/stream` WebSocket pushing incremental updates.
   - Provide `GET /api/v1/health` with component status and uptime.
5. **OTA Updates**
   - Implement `POST /ota` authenticated via pre-shared token or HTTP basic auth.
   - Validate digital signature or checksum before flashing.
   - Roll back on failed update; store last known good firmware slot.
6. **Diagnostics**
   - Serial console commands for sensor calibration, Wi-Fi reset, and debug metrics.
   - LED patterns signalling Wi-Fi state, OTA in progress, and error conditions.

## 7. Configuration & Security
- Store secrets in `include/secrets.h` (ignored by git); template lives in `include/secrets_template.h`.
- Support WPA2 networks, optional fallback to WPA3 where firmware & SDK allow.
- Rate-limit REST requests and require token for mutating actions (calibration, OTA).

## 8. Telemetry & Logging
- Use ESP-IDF logging macros wrapped for Arduino to provide leveled logs.
- Buffer last 100 events in RAM for retrieval via `GET /api/v1/logs`.
- Add persistent crash counters and reason codes in NVS for post-mortem analysis.

## 9. OTA & Deployment Workflow
- Build via `pio run -e esp32c3-devkit`.
- Upload via USB during development (`pio run -t upload`). OTA uses `pio run -t upload --upload-port http://<device>/ota --upload-port-arg token=<token>`.
- Maintain semantic versioning in `include/version.h`; OTA rejects downgrades unless `force=true`.

## 10. Testing & Validation
- Unit tests: sensor fusion math mocked with recorded IMU traces in `test/sensors`.
- HIL tests: bench script verifying heading accuracy via turntable; record actual vs reported heading.
- Network tests: integration harness hitting REST endpoints and validating JSON schema.
- OTA tests: scripted update applying intentionally corrupted image to confirm rollback and error reporting.

## 11. Open Questions
- Final sensor selection (exact IMU/GNSS models).
- Whether HTTPS is mandatory (certificate provisioning complexity).
- Need for BLE fallback for provisioning or telemetry.
