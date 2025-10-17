#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <math.h>
#include "config.h"

// -------- Utilities --------
static inline double deg2rad(double d){ return d * M_PI / 180.0; }
static inline double rad2deg(double r){ return r * 180.0 / M_PI; }

static double haversine_m(double lat1, double lon1, double lat2, double lon2) {
  const double R = 6371000.0;
  const double dlat = deg2rad(lat2 - lat1);
  const double dlon = deg2rad(lon2 - lon1);
  const double a = sin(dlat/2)*sin(dlat/2) +
                   cos(deg2rad(lat1))*cos(deg2rad(lat2)) * sin(dlon/2)*sin(dlon/2);
  return R * 2 * atan2(sqrt(a), sqrt(1 - a));
}

static double initial_bearing_deg(double lat1, double lon1, double lat2, double lon2) {
  double lat1r = deg2rad(lat1), lat2r = deg2rad(lat2);
  double dlonr = deg2rad(lon2 - lon1);
  double y = sin(dlonr) * cos(lat2r);
  double x = cos(lat1r)*sin(lat2r) - sin(lat1r)*cos(lat2r)*cos(dlonr);
  double br = rad2deg(atan2(y, x));
  br = fmod((br + 360.0), 360.0);
  return br;
}

static void deg_per_meter(double lat_deg, double &degLatPerM, double &degLonPerM) {
  degLatPerM = 1.0 / 111320.0;
  degLonPerM = 1.0 / (111320.0 * cos(deg2rad(lat_deg)));
}

static void appendCSV(String &s, const String &f){ if(s.length()) s += ","; s += f; }
static void appendCSVf(String &s, double v, uint8_t dp){
  if (s.length()) s += ",";
  s += String(v, static_cast<unsigned int>(dp));
}

// -------- State --------
WebServer serverA(HTTP_PORT_A);
WebServer serverB(HTTP_PORT_B);

struct Station {
  const char* id;
  double lat, lon, alt;
  uint32_t seq;
  double bearing_deg;
  double width_rad;
  double rssi_db;
};

Station A = { STATION_ID_A, STATION_LAT_A, STATION_LON_A, STATION_ALT_A_M, 1, 0, 0, 0 };
Station B = { STATION_ID_B, STATION_LAT_B, STATION_LON_B, STATION_ALT_B_M, 1, 0, 0, 0 };

// Moving object
const double objStartLat = OBJ_START_LAT;
const double objStartLon = OBJ_START_LON;
const double objEndLat   = OBJ_END_LAT;
const double objEndLon   = OBJ_END_LON;

// Timing
unsigned long simStartMs = 0;
double pathLengthM = 0.0;
double travelTimeS = 0.0;
unsigned long nextTickMs = 0;

// Latest CSV lines (one per 'Kraken')
String lastCsvA;
String lastCsvB;

// -------- Models --------
static double widthModel(double distanceM) {
  return BASE_WIDTH_RAD + K_WIDTH_RAD_PER_M * distanceM;
}
static double rssiModel(double distanceM) {
  if (distanceM < 1.0) distanceM = 1.0;
  double noise = ((double)esp_random() / (double)UINT32_MAX) * (2.0*RSSI_NOISE_DB) - RSSI_NOISE_DB;
  double rssi = RSSI_REF_DB_AT_1M - 20.0 * log10(distanceM) + noise;
  if (rssi < -120.0) rssi = -120.0;
  if (rssi > -10.0)  rssi = -10.0;
  return rssi;
}
static double peakFromRSSI(double rssi) {
  double p = -rssi / PEAK_SCALE_DIV;
  if (p < 0.1) p = 0.1;
  return p;
}

// 360-bin unit-circle spectrum centered at bearing (compass) mapped to unit-circle
static void makeSpectrum(float *out, int nBins, double bearingCompassDeg, double widthRad, double peak, float bg) {
  double unitCenter = fmod(90.0 - bearingCompassDeg + 360.0, 360.0);
  double sigmaDeg = (widthRad * 180.0 / M_PI) / 2.0;
  if (sigmaDeg < 0.5) sigmaDeg = 0.5;
  for (int d = 0; d < nBins; ++d) {
    double delta = fabs(d - unitCenter);
    if (delta > 180.0) delta = 360.0 - delta;
    double gauss = exp(-0.5 * (delta / sigmaDeg) * (delta / sigmaDeg));
    float jitter = 0.9f + 0.2f * (float)esp_random() / (float)UINT32_MAX;
    out[d] = (float)(peak * gauss) + bg * jitter;
  }
}

static String buildKrakenCsvLine(const Station& S, double bearingDeg, double widthRad, double rssiDb,
                                 double gpsHeadingDeg, double compassHeadingDeg,
                                 const float* spectrum, int nBins) {
  String line; line.reserve(2048);
  unsigned long nowMs = millis();
  appendCSV(line, String((uint32_t)(nowMs)));         // timestamp ms
  appendCSV(line, String((int)round(bearingDeg)));    // max DOA compass (0-359)
  double conf = 99.0 * exp(-widthRad); if (conf>99.0) conf=99.0; if(conf<0.0) conf=0.0;
  appendCSVf(line, conf, 1);                          // confidence
  appendCSVf(line, rssiDb, RSSI_DECIMALS);            // RSSI
  appendCSV(line, String(CENTER_FREQ_HZ));            // frequency
  appendCSV(line, ARRAY_TYPE);                        // array type
  appendCSV(line, "50");                              // latency ms (fake)
  appendCSV(line, S.id);                              // station id
  appendCSVf(line, S.lat, LATLON_DECIMALS);           // lat
  appendCSVf(line, S.lon, LATLON_DECIMALS);           // lon
  appendCSVf(line, gpsHeadingDeg, 1);                 // GPS heading
  appendCSVf(line, compassHeadingDeg, 1);             // compass heading
  appendCSV(line, "GPS");                             // main heading source
  // reserved 4 fields
  appendCSV(line, "0"); appendCSV(line, "0"); appendCSV(line, "0"); appendCSV(line, "0");
  // spectrum bins
  for (int i = 0; i < nBins; ++i) appendCSVf(line, spectrum[i], SPEC_DECIMALS);
  return line;
}

static void computeObject(double& outLat, double& outLon, double& uFraction) {
  unsigned long now = millis();
  double elapsedS = (now - simStartMs) / 1000.0;
  if (travelTimeS <= 0.0) uFraction = 1.0;
  else { uFraction = elapsedS / travelTimeS; if (uFraction>1.0) uFraction=1.0; }
  outLat = OBJ_START_LAT + (OBJ_END_LAT - OBJ_START_LAT) * uFraction;
  outLon = OBJ_START_LON + (OBJ_END_LON - OBJ_START_LON) * uFraction;
}

static void updateOneStation(Station& S, double objLat, double objLon, String& outCsv) {
  double dist = haversine_m(S.lat, S.lon, objLat, objLon);
  double bearing = initial_bearing_deg(S.lat, S.lon, objLat, objLon);
  double width = widthModel(dist);
  double rssi  = rssiModel(dist);
  double peak  = peakFromRSSI(rssi);
  float spectrum[N_BINS];
  makeSpectrum(spectrum, N_BINS, bearing, width, peak, BACKGROUND_LEVEL);
  outCsv = buildKrakenCsvLine(S, bearing, width, rssi, bearing, bearing, spectrum, N_BINS);
  S.bearing_deg = bearing;
  S.width_rad = width;
  S.rssi_db = rssi;
  S.seq++;
}

// -------- HTTP Handlers --------
void handleRootA(){ serverA.send(200, "text/plain", "Kraken A: /DOA_value.html  /status.json"); }
void handleRootB(){ serverB.send(200, "text/plain", "Kraken B: /DOA_value.html  /status.json"); }

void handleDOA_A(){ serverA.send(200, "text/html", lastCsvA); }
void handleDOA_B(){ serverB.send(200, "text/html", lastCsvB); }

void handleStatusA(){
  String json = "{\"id\":\""+String(A.id)+"\",\"lat\":"+String(A.lat,6)+",\"lon\":"+String(A.lon,6)+
                ",\"bearing\":"+String(A.bearing_deg,1)+",\"rssi\":"+String(A.rssi_db,1)+"}";
  serverA.send(200, "application/json", json);
}
void handleStatusB(){
  String json = "{\"id\":\""+String(B.id)+"\",\"lat\":"+String(B.lat,6)+",\"lon\":"+String(B.lon,6)+
                ",\"bearing\":"+String(B.bearing_deg,1)+",\"rssi\":"+String(B.rssi_db,1)+"}";
  serverB.send(200, "application/json", json);
}

// -------- Setup & Loop --------
void setup() {
  Serial.begin(115200);
  delay(200);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting WiFi");
  for (int i=0; i<60 && WiFi.status()!=WL_CONNECTED; ++i) { delay(250); Serial.print("."); }
  Serial.println();
  if (WiFi.status()!=WL_CONNECTED) { Serial.println("WiFi failed, rebooting"); delay(3000); ESP.restart(); }
  Serial.print("WiFi OK. IP: "); Serial.println(WiFi.localIP());

  // Path metrics
  pathLengthM = haversine_m(OBJ_START_LAT, OBJ_START_LON, OBJ_END_LAT, OBJ_END_LON);
  travelTimeS = (SPEED_MPS>0.0) ? (pathLengthM / SPEED_MPS) : 0.0;
  simStartMs = millis();
  nextTickMs = simStartMs;

  // HTTP A
  serverA.on("/", handleRootA);
  serverA.on("/DOA_value.html", handleDOA_A);
  serverA.on("/status.json", handleStatusA);
  serverA.begin();
  Serial.printf("HTTP A started on port %d\n", HTTP_PORT_A);

  // HTTP B
  serverB.on("/", handleRootB);
  serverB.on("/DOA_value.html", handleDOA_B);
  serverB.on("/status.json", handleStatusB);
  serverB.begin();
  Serial.printf("HTTP B started on port %d\n", HTTP_PORT_B);

  Serial.printf("Endpoints:\n  A: http://%s:%d/DOA_value.html\n  B: http://%s:%d/DOA_value.html\n",
                WiFi.localIP().toString().c_str(), HTTP_PORT_A,
                WiFi.localIP().toString().c_str(), HTTP_PORT_B);
}

void loop() {
  serverA.handleClient();
  serverB.handleClient();

  unsigned long now = millis();
  if (now < nextTickMs) return;
  nextTickMs = now + (unsigned long)(BURST_PERIOD_S * 1000.0);
  if (BURST_JITTER_MS>0.0) {
    long j = (long)((((double)esp_random()/(double)UINT32_MAX)*2.0 - 1.0) * BURST_JITTER_MS);
    long nt = (long)nextTickMs + j; if (nt>0) nextTickMs = (unsigned long)nt;
  }

  double objLat, objLon, u;
  // compute current object position
  {
    unsigned long nowMs = millis();
    double elapsedS = (nowMs - simStartMs) / 1000.0;
    if (travelTimeS <= 0.0) u = 1.0;
    else { u = elapsedS / travelTimeS; if (u>1.0) u=1.0; }
    objLat = OBJ_START_LAT + (OBJ_END_LAT - OBJ_START_LAT) * u;
    objLon = OBJ_START_LON + (OBJ_END_LON - OBJ_START_LON) * u;
  }

  // Update both stations
  updateOneStation(A, objLat, objLon, lastCsvA);
  updateOneStation(B, objLat, objLon, lastCsvB);

  if (u >= 1.0) {
    if (ON_REACH_END == 0) {
      // stop updating; keep serving last values
    } else if (ON_REACH_END == 2) {
      simStartMs = millis();
      A.seq = 1; B.seq = 1;
    } else {
      // hold: keep emitting same last point
    }
  }
}
