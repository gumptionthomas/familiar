#pragma once

// FW_VERSION is injected at build time by tools/git_version.py. This fallback
// keeps the firmware compiling when that script did not run -- no git, a
// source zip, or a build outside the PlatformIO device environment. A missing
// version is an inconvenience; a firmware that will not compile is not.
#ifndef FW_VERSION
#define FW_VERSION "unknown"
#endif
