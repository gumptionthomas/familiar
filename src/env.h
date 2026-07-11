#pragma once
// ENV-III HAT (SHT30 temp/humidity + QMP6988 pressure) read on I2C bus **Wire**
// (I2C0), HAT header pins SDA=G0, SCL=G26.
//
// CRITICAL: on the M5StickC Plus the M5 library puts the INTERNAL bus (AXP192,
// IMU/MPU6886, RTC) on **Wire1** (I2C1, pins 21/22 — see RTC.cpp / MPU6886.cpp
// in the M5StickCPlus lib). So the free peripheral for the HAT is Wire (I2C0),
// NOT Wire1. Using Wire1 here reconfigures I2C1 onto G0/G26 and kills the IMU
// (constant "dizzy" from garbage shake reads) plus the RTC/AXP. Wire (I2C0) is
// otherwise unused by the M5 lib, so it's ours.
//
// Header-only, included once from main.cpp (same pattern as data.h / stats.h).
#include <Arduino.h>
#include <Wire.h>
#include "M5UnitENV.h"

#define ENV_SDA      0
#define ENV_SCL      26
#define ENV_SHT_ADDR 0x44   // SHT30
// SHT3X::update() blocks ~250ms (single-shot measure + settle), so poll
// infrequently — ambient temp/humidity/pressure drift slowly — to keep that
// stall from hitching the pet animation / button polling.
#define ENV_POLL_MS  5000
// QMP6988's I2C address depends on an SDO strap and varies by HAT batch:
// 0x70 (SLAVE_ADDRESS_L) or 0x56 (SLAVE_ADDRESS_H). Our unit answers on 0x70;
// try L first, then H, so either variant works.

static SHT3X    _envSht;
static QMP6988  _envQmp;
static bool     _envPresent = false;
static float    _envFTemp = 0, _envHum = 0, _envPa = 0;
static uint32_t _envNextPoll = 0;

// Read the ENV-III HAT. begin() initializes Wire (I2C0) on G0/G26 and returns
// true only if the chip answers (it probes with i2c.exist), so it doubles as
// presence detection. Never touches Wire1 (the internal AXP/IMU/RTC bus).
inline void envInit() {
  bool sht = _envSht.begin(&Wire, ENV_SHT_ADDR, ENV_SDA, ENV_SCL, 400000U);
  bool qmp = _envQmp.begin(&Wire, QMP6988_SLAVE_ADDRESS_L, ENV_SDA, ENV_SCL, 400000U)
          || _envQmp.begin(&Wire, QMP6988_SLAVE_ADDRESS_H, ENV_SDA, ENV_SCL, 400000U);
  _envPresent = sht && qmp;

  // Bring-up diagnostic: Wire is up now, so scan it and report what answers on
  // G0/G26 — distinguishes "wrong bus/pins" from "dead sensor" if it misbehaves.
  Serial.print("[env] I2C scan on G0/G26 (Wire/I2C0):");
  int n = 0;
  for (uint8_t a = 0x08; a < 0x78; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { Serial.printf(" 0x%02X", a); n++; }
  }
  Serial.println(n ? "" : " (nothing found)");
  Serial.printf("[env] sht30=%s qmp6988=%s -> %s\n",
                sht ? "ok" : "MISSING", qmp ? "ok" : "MISSING",
                _envPresent ? "present" : "NOT FOUND");
}

// Throttled read; call every loop. No-op if the HAT wasn't detected.
inline void envPoll() {
  if (!_envPresent) return;
  uint32_t now = millis();
  if ((int32_t)(now - _envNextPoll) < 0) return;
  _envNextPoll = now + ENV_POLL_MS;
  if (_envSht.update()) { _envFTemp = _envSht.fTemp; _envHum = _envSht.humidity; }
  // QMP6988::update() returns true even on a failed read (pressure comes back
  // 0); keep the last good value instead of flashing a bogus 0 hPa.
  if (_envQmp.update() && _envQmp.pressure > 0) { _envPa = _envQmp.pressure; }
}

inline bool envPresent()     { return _envPresent; }
inline int  envTempF()       { return (int)lroundf(_envFTemp); }
inline int  envHumidityPct() { return (int)lroundf(_envHum); }
inline int  envPressureHpa() { return (int)lroundf(_envPa / 100.0f); }
