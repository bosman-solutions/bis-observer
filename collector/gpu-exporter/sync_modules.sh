#!/usr/bin/env bash
# Vendor the SCC modules the exporter composes, from the scc repo into this build
# context. The scc repo stays the source of truth; the copies are gitignored.
set -euo pipefail
SCC="${SCC_DIR:-$HOME/claude/scc}"
HERE="$(cd "$(dirname "$0")" && pwd)"
for f in gpu_probe.py vmctl.py; do
  if [[ ! -f "$SCC/$f" ]]; then
    echo "ERROR: $SCC/$f not found (set SCC_DIR if scc lives elsewhere)" >&2
    exit 1
  fi
  cp "$SCC/$f" "$HERE/$f"
  echo "vendored $f from $SCC"
done
