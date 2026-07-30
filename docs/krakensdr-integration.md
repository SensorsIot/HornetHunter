# KrakenSDR Integration — Remote Management & DoA Feed

How a Kraken Pi station talks to the `krakensdr_doa` software running beside it:
how bearings come out, and how configuration goes in.

> **Confidence key**
> - ✅ **verified against a live station** — probed on `192.168.0.127`
>   (`station_id` `hb9bla-st4`), read-only
> - 📖 **read from upstream source** — `krakensdr_doa` on GitHub; not confirmed on
>   our hardware
> - ⚠️ **unverified** — needs a measurement before it is relied upon
>
> Source: [krakenrf/krakensdr_doa](https://github.com/krakenrf/krakensdr_doa)

> **The deployed version is not `main`.** The middleware REST API documented
> upstream (`GET`/`POST http://<pi>:8042/settings`) **returns 404 on our station**
> — port 8042 is Express, but that route does not exist in the installed build.
> The working route today is **miniserve on port 8081**. Plan for 8081; treat 8042
> as an upgrade path. See §3.

---

## 1. The services, as actually observed

| Service | Port | Status on our station | Purpose |
|---------|------|----------------------|---------|
| Dash UI | 8080 | ✅ open | operator web UI; browser only |
| Middleware (Express) | 8042 | ✅ open, but **`/settings` 404s** | upstream documents settings REST here |
| Middleware WebSocket | 8021 | ✅ **closed** | upstream fans DoA records out here |
| miniserve | 8081 | ✅ open, serves `settings.json` | **the route that works** |

Port 8021 being closed may be a consequence of the current `doa_data_format`
rather than the feature's absence — upstream starts the WebSocket server
conditionally. ⚠️ Re-probe after switching the format (§2).

## 2. Reading bearings

The station currently runs **`doa_data_format = "Full POST"`** ✅ — which is the one
mode to avoid. Its code path calls
`requests.get("https://ip.seeip.org/jsonip?")` **synchronously, with no timeout,
inside the processing loop, once per second**. 📖 (sigproc:866) On a field
deployment with no internet this stalls the DSP thread every second. Changing this
is the first configuration action for any station.

### Preferred: WebSocket push 📖

Set `doa_data_format = "Kraken Pro Local"`. The DSP then POSTs every measurement to
the middleware's `/doapost`, which broadcasts it to all WebSocket clients on
port 8021. The station agent **subscribes**; it never polls.

```
ws://127.0.0.1:8021   ->   one JSON object per DoA measurement
```

Record fields (`wr_json`, sigproc:1161): `station_id`, `tStamp`, `gps_timestamp`,
`latitude`, `longitude`, `gpsBearing`, `speed`, `radioBearing`, `conf`, `power`,
`freq`, `antType`, `latency`, `processing_time`, `doaArray`, `adc_overdrive`,
`num_corr_sources`, `snr`. 📖

**Must be validated on hardware before we depend on it** (⚠️), since 8021 is
currently closed and the deployed build differs from upstream in at least one known
way (§3).

### Fallback: `DOA_value.html` polling 📖

A single CSV line rewritten in place each cycle and served over HTTP. Polling
rather than push, and the bearing is written as `360 − θ₀` rather than `θ₀` — an
easy sign error. Viable if the WebSocket path proves unavailable.

### Do not use

- **`Kraken Pro Remote`** — relays measurements to `wss://map.krakenrf.com:2096`,
  a third-party cloud. 📖
- **`Full POST`** — the synchronous public-IP lookup described above. 📖

## 3. Writing configuration

### What works today: miniserve on 8081 ✅

```bash
curl http://<station>:8081/settings.json                    # verified working
curl -F "path=@settings.json" http://<station>:8081/upload\?path\=/   # upload
```

The read is confirmed on our station (HTTP 200, 4498 bytes). The upload half is
documented upstream but **not tested here** ⚠️ — it writes to a live station, so it
needs a deliberate test, not a probe.

### What upstream documents: middleware REST on 8042 📖

```bash
curl http://<station>:8042/settings
curl -X POST -H 'Content-Type: application/json' -d @new.json http://<station>:8042/settings
```

`POST /settings` writes `settings.json` and stamps `ext_upd_flag = true`, and works
without "remote mode" being enabled. **Returns 404 on our station** ✅ — the
installed middleware predates the route. Prefer it once the stations are updated;
it is the cleaner interface.

### Either way, changes apply live 📖

`settings_change_watcher()` re-arms itself on a **0.5 s `Timer`**, compares
`settings.json`'s mtime against the loaded timestamp, and applies changes in
place. If `center_freq` moved it calls `config_daq_rf(center_freq, gain)`, retuning
without bouncing anything. (`_ui/_web_interface/utils.py:245`, `:381`, `:414`)

So a configuration push is **not** a disruptive operation: no restart, no
measurement outage of consequence, no operator warning needed. Worst-case apply
latency is ~0.5 s plus retune settling (⚠️ settling not characterised).

**Read-back works**, which is what makes end-to-end verification possible: the
station can compute a checksum from what the KrakenSDR actually holds rather than
from what it was told to apply. (Contrast the LoRa DTU, whose `AT+KEY` is
write-only — see [lora-dtu-sx1262.md](lora-dtu-sx1262.md).)

## 4. The parameter set

**The live schema has 158 fields, not the 52 in upstream's sample
`_nodejs/settings.json`.** ✅ Any field registry must be generated from a live
station's settings, not from the upstream sample.

Extra field families present live but absent from the upstream sample: ✅

- per-VFO, ×16 each: `vfo_demod_N`, `vfo_iq_N`, `vfo_squelch_mode_N`,
  `vfo_fir_order_factor_N`
- `doa_decorrelation_method`, `expected_num_of_sources`, `max_demod_timeout`
- `vfo_default_demod`, `vfo_default_iq`, `vfo_default_squelch_mode`
- `en_system_control`, `en_beta_features`, `en_remote_control`
- `gps_fixed_heading`, `gps_min_speed`, `gps_min_speed_duration`
- `mapping_server_url`, `ext_upd_flag`

And **`en_fbavg` does not exist live** ✅ — it has been superseded by
`doa_decorrelation_method` (`Off` / `FBA` / `TOEP` / `FBSS`).

### Live values, for reference ✅

| Field | Value | Note |
|-------|-------|------|
| `center_freq` | `148.524` | **MHz** — the tag frequency |
| `vfo_freq_0` | `148524000.0` | **Hz** — note the unit difference |
| `vfo_bw_0` | `100000` | 100 kHz |
| `vfo_squelch_0` | `-62` | |
| `uniform_gain` | `0.9` | |
| `ant_arrangement` | `UCA` | |
| `ant_spacing_meters` | `0.53` | |
| `doa_method` | `MUSIC` | |
| `spectrum_calculation` | `Single` | required for squelch |
| `dsp_decimation` | `2` | |
| `active_vfos` | `1` | |
| `location_source` | `gpsd` | **live GPS**, not `Static` |
| `heading` | `0.0` | array aligned manually |
| `doa_data_format` | `Full POST` | to be changed (§2) |

**Unit trap:** `center_freq` is in **MHz**, `vfo_freq_N` is in **Hz**. ✅

**`location_source = "gpsd"`** confirms station position is live, so position may
change during operation. The `gps_fixed_heading` / `gps_min_speed` fields exist
because GPS gives *course over ground*, which is meaningless when stationary — the
array heading is a separate quantity, set here by manual alignment to 0°.

## 5. Consequences for our wire contract

`shared/messages.py`'s `BearingReport` has been revised to match (FSD Appendix D).
Fields the feed offers beyond bearing and confidence:

- `power` (max power level) and `snr` — quality beyond `conf`
- `adc_overdrive` — front-end saturation flag
- `num_corr_sources` — multipath / multiple-emitter indicator
- `latency`, `processing_time` — measurement age, needed to timestamp correctly
- `gpsBearing`, `speed`, `gps_timestamp` — live position data
- `doaArray` — the full DoA spectrum; too large for LoRa, unused in v1

## 6. Open items

- Whether port 8021 opens once `doa_data_format` is `Kraken Pro Local`. ⚠️
  **Blocking** — it decides the read path.
- Whether the 8081 `/upload` route accepts our writes and the watcher picks them
  up. ⚠️ **Blocking** — it decides the write path.
- The installed middleware / DSP version, and whether updating it would provide
  `8042/settings`. ⚠️
- Retune settling time after `config_daq_rf`. ⚠️
- `conf` range and scale — needed before quantising it for the LoRa link. ⚠️
- Whether `POST`/upload validates input, or will happily write a settings file that
  wedges the DSP. ⚠️
