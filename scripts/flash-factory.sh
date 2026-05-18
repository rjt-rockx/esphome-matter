#!/usr/bin/env bash
# Flash the factory NVS partition at the `fctry` offset.
# See components/matter/partitions.csv for the canonical offset (0x3C0000).
set -euo pipefail

PART_BIN="${1:?usage: flash-factory.sh <partition.bin> [/dev/ttyUSB0]}"
PORT="${2:-/dev/ttyUSB0}"
OFFSET="${OFFSET:-0x3C0000}"

esptool.py -p "$PORT" -b 460800 write_flash "$OFFSET" "$PART_BIN"
