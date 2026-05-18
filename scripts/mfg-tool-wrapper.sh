#!/usr/bin/env bash
# Wrap esp-matter-mfg-tool with sensible defaults for the CSA test DAC.
# Produces a factory partition .bin that's flashed at the `fctry` offset
# (see components/matter/partitions.csv).
#
# Requires: esp-matter installed, ESP_MATTER_PATH exported, python venv active.
set -euo pipefail

VENDOR_ID="${VENDOR_ID:-0xFFF2}"
PRODUCT_ID="${PRODUCT_ID:-0x8001}"
DISCRIMINATOR="${DISCRIMINATOR:-3840}"
PASSCODE="${PASSCODE:-20202021}"
OUT_DIR="${OUT_DIR:-./out/factory}"

if [[ -z "${ESP_MATTER_PATH:-}" ]]; then
  echo "ESP_MATTER_PATH is not set. Source esp-matter's export.sh first." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

esp-matter-mfg-tool \
  -v "$VENDOR_ID" \
  -p "$PRODUCT_ID" \
  --vendor-name "ESPHome" \
  --product-name "Matter Plug Spike" \
  --serial-num "esphome-matter-001" \
  --hw-ver 1 \
  --hw-ver-str "v0" \
  --passcode "$PASSCODE" \
  --discriminator "$DISCRIMINATOR" \
  -cd "$ESP_MATTER_PATH/connectedhomeip/connectedhomeip/credentials/test/certification-declaration/Chip-Test-CD-FFF2-8001.der" \
  -dac-cert "$ESP_MATTER_PATH/connectedhomeip/connectedhomeip/credentials/test/attestation/Chip-Test-DAC-FFF2-8001-0009-Cert.der" \
  -dac-key "$ESP_MATTER_PATH/connectedhomeip/connectedhomeip/credentials/test/attestation/Chip-Test-DAC-FFF2-8001-0009-Key.der" \
  -pai "$ESP_MATTER_PATH/connectedhomeip/connectedhomeip/credentials/test/attestation/Chip-Test-PAI-FFF2-8001-Cert.der" \
  -o "$OUT_DIR"

echo "Factory partition written under $OUT_DIR"
echo "Flash with: scripts/flash-factory.sh <path-to-partition.bin> [/dev/ttyUSB0]"
