#pragma once
#include <stdint.h>
#include <string.h>
#include <ArduinoJson.h>

#define FEED_MAX_LINES 8
#define FEED_LINE_CAP  92          // 91 chars + NUL

// Copy `entries` into `lines`; return true if the feed actually CHANGED (the
// count differs, or any line differs). The caller bumps lineGen on true.
//
// Do NOT reintroduce the old shortcut of comparing lines[n-1] against `msg`.
// `msg` is a short *status* field (REFERENCE.md) truncated to 23 chars, so any
// line longer than that mismatched on EVERY heartbeat -- lineGen ticked forever,
// which woke the screen every 10s and stopped the buddy ever reaching clock mode.
inline bool feedApplyEntries(char lines[FEED_MAX_LINES][FEED_LINE_CAP],
                             uint8_t* nLines, JsonArrayConst la) {
  uint8_t n = 0;
  bool changed = false;
  for (JsonVariantConst v : la) {
    // Entries arrive oldest-first (newest last), so this cap drops the NEWEST
    // entries, not the oldest. Safe only because the bridge caps max_entries
    // at 6 (< FEED_MAX_LINES=8); if that default ever grows, this must be
    // changed to drop from the front instead.
    if (n >= FEED_MAX_LINES) break;
    const char* s = v.as<const char*>();
    if (!s) s = "";
    if (strncmp(lines[n], s, FEED_LINE_CAP - 1) != 0) changed = true;
    strncpy(lines[n], s, FEED_LINE_CAP - 1);
    lines[n][FEED_LINE_CAP - 1] = 0;
    n++;
  }
  if (n != *nLines) changed = true;
  *nLines = n;
  return changed;
}
