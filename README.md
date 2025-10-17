# 🐝 HornetHunter

> Advanced Radio Direction Finding System for Tracking Invasive Hornets

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-ESP32--S3-green.svg)](https://www.espressif.com/)
[![Build](https://img.shields.io/badge/build-PlatformIO-orange.svg)](https://platformio.org/)

## 📋 Overview

HornetHunter is a comprehensive radio direction finding (RDF) system designed to track and locate invasive hornets equipped with radio transmitters. The project combines precision antenna positioning, KrakenSDR-based direction-of-arrival (DoA) measurement, and simulation tools to enable effective tracking and eradication of invasive hornet populations.

The system uses multiple ground stations to triangulate transmitter positions, with each station featuring:
- **Precision orientation tracking** using IMU and GNSS sensors
- **Real-time telemetry** over Wi-Fi networks
- **Direction-of-arrival measurements** from KrakenSDR hardware
- **Simulation capabilities** for testing and development

---

## 🗂️ Project Structure

```
HornetHunter/
├── AntennaPositioner/     # ESP32-S3 antenna orientation controller
├── KrakenSimmulator/      # Dual KrakenSDR DoA endpoint simulator
└── README.md              # This file
```

---

## 📡 AntennaPositioner

### Overview
The AntennaPositioner is an ESP32-S3-based controller that continuously measures antenna heading, pitch, roll, and GPS position, exposing real-time telemetry over Wi-Fi for integration with direction-finding systems.

### ✨ Key Features
- 🧭 **Precision Orientation**: 9-DoF IMU with magnetometer fusion (±2° accuracy)
- 📍 **GPS Positioning**: Real-time latitude, longitude, altitude tracking
- 🌐 **Wi-Fi Connectivity**: REST API + WebSocket streaming (≤500ms latency)
- 🔄 **OTA Updates**: Secure over-the-air firmware updates
- 🛡️ **High Reliability**: ≥95% uptime with watchdog protection

### 🔧 Hardware Requirements
- **MCU**: ESP32-S3 Super Mini (Wi-Fi + BLE)
- **IMU**: 9-DoF sensor (BNO055, ICM-20948 + QMC5883L)
- **GNSS**: UART-based GPS receiver (NMEA)
- **Power**: 5V USB-C with optional LiPo battery
- **Indicators**: Status LEDs and provisioning button

### 🚀 Quick Start
```bash
cd AntennaPositioner
pio run -e esp32s3-devkit
pio run -t upload
```

### 📊 API Endpoints
- `GET /api/v1/status` - Current heading, orientation, position, and velocity
- `GET /api/v1/stream` - WebSocket live telemetry stream
- `GET /api/v1/health` - System health and uptime
- `POST /ota` - Authenticated OTA firmware update

### 🔐 Configuration
1. Copy `include/secrets_template.h` to `include/secrets.h`
2. Configure Wi-Fi credentials and OTA token
3. On first boot, device creates AP `AntennaPositioner-XXXX` for provisioning

---

## 🛰️ KrakenSimulator

### Overview
KrakenSimulator is a deterministic dual-endpoint HTTP server that emulates two independent KrakenSDR direction-of-arrival systems. It simulates a moving target along a configurable path, allowing downstream software to be developed and tested without physical hardware.

### ✨ Key Features
- 🎯 **Dual Endpoints**: Two independent KrakenSDR stations on separate ports
- 📈 **Realistic Models**: Bearing, RSSI, width, and spectrum simulation
- ⏱️ **Configurable Motion**: Linear object path with adjustable speed
- 🔄 **Real-time Updates**: 5-10 Hz sample rate with history buffers
- 🧪 **Drop-in Replacement**: JSON format matches real KrakenSDR API

### 🔧 Requirements
- **Platform**: ESP32-S3 (or any platform with HTTP server capability)
- **Network**: HTTP server on two configurable ports
- **Memory**: Sufficient for history buffers and spectrum arrays

### 🚀 Quick Start
```bash
cd KrakenSimmulator
pio run -e esp32s3-devkit
pio run -t upload
```

### 📊 API Endpoints
- `GET /api/v1/doa` - Latest direction-of-arrival measurement
- `GET /api/v1/doa?history=N` - Last N samples
- `GET /api/v1/doa?format=csv` - CSV format output
- `GET /api/v1/metadata` - Station configuration and constants
- `GET /healthz` - Health check endpoint

### ⚙️ Configuration
Edit `include/config.h` to set:
- **Network**: `HTTP_PORT_A` (8081), `HTTP_PORT_B` (8082)
- **Stations**: Station IDs, coordinates, altitudes
- **Object Path**: Start/end positions and duration
- **Signal**: Center frequency, array type, update rate
- **Models**: RSSI reference, width parameters, spectrum bins

### 📋 Example Response
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
  "spectrum": [0.08, 0.10, 0.12, "..."]
}
```

---

## 🎯 Use Cases

### Field Deployment
1. Deploy multiple AntennaPositioner units at known locations
2. Connect each to a KrakenSDR direction-finding system
3. Collect bearing measurements from multiple stations
4. Triangulate transmitter position using multi-station data

### Development & Testing
1. Run KrakenSimulator to generate synthetic DoA measurements
2. Test tracking algorithms without physical hardware
3. Validate triangulation logic with known object paths
4. Stress-test client software with configurable scenarios

---

## 🛠️ Development

### Prerequisites
- [PlatformIO](https://platformio.org/) installed
- ESP32-S3 development board
- USB cable for programming

### Building
```bash
# Build all projects
pio run

# Build specific project
cd AntennaPositioner && pio run
cd KrakenSimmulator && pio run
```

### Testing
Each sub-project includes its own testing framework:
- **Unit tests**: Sensor fusion algorithms and network handlers
- **Integration tests**: Hardware-in-the-loop validation
- **Network tests**: API endpoint schema validation

---

## 📚 Documentation

Detailed functional specifications for each component:
- [AntennaPositioner FSD](AntennaPositioner/AntennaPositioner-FSD.md)
- [KrakenSimulator FSD](KrakenSimmulator/KrakenSim_FSD.md)

---

## 🤝 Contributing

Contributions are welcome! Please ensure:
- Code follows existing style conventions
- Changes include appropriate tests
- Documentation is updated for new features
- Commit messages are clear and descriptive

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## 🔗 Related Projects

- [KrakenSDR](https://www.krakenrf.com/) - 5-channel coherent radio direction finder
- [PlatformIO](https://platformio.org/) - Cross-platform embedded development
- [ESP-IDF](https://github.com/espressif/esp-idf) - Espressif IoT Development Framework

---

## 📞 Support

For issues, questions, or contributions, please open an issue on the GitHub repository.

---

**Built with dedication to protecting ecosystems from invasive species** 🌍🐝
