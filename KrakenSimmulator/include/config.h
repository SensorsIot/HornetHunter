#pragma once
// ======== Wi-Fi ========
#define WIFI_SSID            "YOUR_WIFI_SSID"
#define WIFI_PASS            "YOUR_WIFI_PASSWORD"

// ======== HTTP Servers (one port per fake Kraken) ========
#define HTTP_PORT_A          8081
#define HTTP_PORT_B          8082

// ======== Station Identity & Position ========
#define STATION_ID_A         "FAKE1"
#define STATION_LAT_A        47.474242
#define STATION_LON_A        7.765962
#define STATION_ALT_A_M      400.0

#define STATION_ID_B         "FAKE2"
// Explicitly set B to ~100 m east of A at lat 47.474242
#define STATION_LAT_B        47.474242
#define STATION_LON_B        7.767291   // computed ≈ A + 0.001329°
#define STATION_ALT_B_M      400.0
// (Auto-place by separation not used in this HTTP build)

// ======== Moving Object (independent path) ========
#define OBJ_START_LAT        47.474904
#define OBJ_START_LON        7.766416
#define OBJ_END_LAT          47.473120
#define OBJ_END_LON          7.766545

// ======== Motion & cadence ========
#define SPEED_MPS            6.0       // hornet ground speed (m/s)
#define BURST_PERIOD_S       1.0       // 1 Hz updates
#define BURST_JITTER_MS      20.0      // ±ms jitter

// End-of-path behavior: 0=stop, 1=hold last point, 2=loop to start
#define ON_REACH_END         0

// ======== Message / Spectrum ========
#define CENTER_FREQ_HZ       148524000
#define ARRAY_TYPE           "ULA"
// Kraken App CSV typically uses 360 unit-circle bins:
#define N_BINS               360
// If your receiver expects 181, change the line above to:  // #define N_BINS 181
#define BACKGROUND_LEVEL     0.05f

// ======== Models (width & RSSI) ========
// width(rad) = BASE_WIDTH_RAD + K_WIDTH_RAD_PER_M * distance_m
#define BASE_WIDTH_RAD       0.15
#define K_WIDTH_RAD_PER_M    0.004
// RSSI(dBFS) ~ RSSI_REF_DB_AT_1M - 20*log10(distance_m) + noise
#define RSSI_REF_DB_AT_1M    -30.0
#define RSSI_NOISE_DB        2.0
#define PEAK_SCALE_DIV       20.0

// ======== Realism Tweaks ========
#define POSITION_JITTER_M    0.0       // ±meters per tick
#define TIMESTAMP_JITTER_MS  0.0       // extra ±ms on top of BURST_JITTER_MS

// ======== Formatting / CSV compatibility ========
#define BEARING_DECIMALS     0
#define WIDTH_DECIMALS       6
#define RSSI_DECIMALS        2
#define LATLON_DECIMALS      6
#define ALT_DECIMALS         1
#define SPEC_DECIMALS        2
