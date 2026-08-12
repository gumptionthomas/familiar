#pragma once

// FW_VERSION is injected at build time by tools/git_version.py. This fallback
// keeps the firmware compiling when that script did not run -- no git, a
// source zip, or a build outside the PlatformIO device environment. A missing
// version is an inconvenience; a firmware that will not compile is not.
#ifndef FW_VERSION
#define FW_VERSION "unknown"
#endif

#include <stdio.h>
#include <string.h>

// The DEVICE info page shows ~21 columns; the "  fw       " label eats 11, so
// a "-dirty" suffix (24 columns) wraps and is overpainted by the next line.
// Show it as a single "*" there. The status reply keeps the full string --
// that one is machine-read and must stay unambiguous.
inline const char* fwVersionDisplay() {
  static char buf[24];
  static bool init = false;
  if (!init) {
    snprintf(buf, sizeof(buf), "%s", FW_VERSION);
    char* d = strstr(buf, "-dirty");
    if (d) { d[0] = '*'; d[1] = '\0'; }
    init = true;
  }
  return buf;
}
