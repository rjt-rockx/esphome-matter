#!/usr/bin/env bash
# Wrap an ESPHome firmware image into a Matter OTA image (.ota) using
# connectedhomeip's src/app/ota_image_tool.py. See docs/matter-ota.md.
#
# Usage:
#   tools/make_ota_image.sh --firmware <firmware.ota.bin> --version <n> \
#       [--vendor-id 0xFFF1] [--product-id 0x8000] [--version-string 1.0.1] \
#       [--min-version <n>] [--max-version <n>] [--release-notes <url>] \
#       [--ota-tool <path/to/ota_image_tool.py>] --output <out.ota>
#
# --version must equal `software_version:` in the YAML that built the firmware
# and be higher than the version running on the device. --vendor-id/--product-id
# must equal the device's (matter: vendor_id / product_id; defaults are the
# Matter test IDs 0xFFF1 / 0x8000).
#
# ota_image_tool.py is looked up in, in order: --ota-tool, $OTA_IMAGE_TOOL,
# $ESP_MATTER_PATH/connectedhomeip/connectedhomeip/src/app/ota_image_tool.py,
# $CONNECTEDHOMEIP_PATH/src/app/ota_image_tool.py, or the esp-matter checkout
# ESP-IDF's managed_components (if the build directory is passed via --build-dir).
# If none exists, it is downloaded (with its matter.tlv helper) from GitHub.
set -euo pipefail

firmware=""
output=""
vendor_id="0xFFF1"
product_id="0x8000"
version=""
version_string=""
min_version=""
max_version=""
release_notes=""
digest="sha256"
tool="${OTA_IMAGE_TOOL:-}"
build_dir=""

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --firmware) firmware="$2"; shift 2 ;;
    -o|--output) output="$2"; shift 2 ;;
    --vendor-id) vendor_id="$2"; shift 2 ;;
    --product-id) product_id="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    --version-string) version_string="$2"; shift 2 ;;
    --min-version) min_version="$2"; shift 2 ;;
    --max-version) max_version="$2"; shift 2 ;;
    --release-notes) release_notes="$2"; shift 2 ;;
    --digest) digest="$2"; shift 2 ;;
    --ota-tool) tool="$2"; shift 2 ;;
    --build-dir) build_dir="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

[ -n "$firmware" ] && [ -n "$output" ] && [ -n "$version" ] || usage
[ -f "$firmware" ] || { echo "Firmware not found: $firmware" >&2; exit 1; }
case "$version" in ''|*[!0-9]*) echo "--version must be a positive integer" >&2; exit 1 ;; esac
[ -n "$version_string" ] || version_string="$version"
if [ "${#version_string}" -gt 64 ]; then
  echo "--version-string is limited to 64 characters" >&2; exit 1
fi

find_tool() {
  local c
  for c in \
    "$tool" \
    "${ESP_MATTER_PATH:+$ESP_MATTER_PATH/connectedhomeip/connectedhomeip/src/app/ota_image_tool.py}" \
    "${CONNECTEDHOMEIP_PATH:+$CONNECTEDHOMEIP_PATH/src/app/ota_image_tool.py}"; do
    [ -n "$c" ] && [ -f "$c" ] && { echo "$c"; return 0; }
  done
  if [ -n "$build_dir" ]; then
    c=$(find "$build_dir" -path '*src/app/ota_image_tool.py' 2>/dev/null | head -n1 || true)
    [ -n "$c" ] && { echo "$c"; return 0; }
  fi
  return 1
}

if ! tool=$(find_tool); then
  # ota_image_tool.py imports the `matter.tlv` helper from
  # src/controller/python, so fetch both into a cache directory.
  ref="${CONNECTEDHOMEIP_REF:-master}"
  cache="${XDG_CACHE_HOME:-$HOME/.cache}/esphome-matter/ota_image_tool-$ref"
  base="https://raw.githubusercontent.com/project-chip/connectedhomeip/$ref"
  if [ ! -f "$cache/src/app/ota_image_tool.py" ]; then
    echo "ota_image_tool.py not found locally; downloading from connectedhomeip ($ref)..." >&2
    mkdir -p "$cache/src/app" "$cache/src/controller/python/matter/tlv"
    curl -fsSL "$base/src/app/ota_image_tool.py" -o "$cache/src/app/ota_image_tool.py"
    curl -fsSL "$base/src/controller/python/matter/tlv/__init__.py" \
      -o "$cache/src/controller/python/matter/tlv/__init__.py"
    # Some revisions keep tlv in chip/tlv; the tool's own sys.path lines decide.
    touch "$cache/src/controller/python/matter/__init__.py"
  fi
  tool="$cache/src/app/ota_image_tool.py"
fi

args=(create -v "$vendor_id" -p "$product_id" -vn "$version" -vs "$version_string" -da "$digest")
[ -n "$min_version" ] && args+=(-mi "$min_version")
[ -n "$max_version" ] && args+=(-ma "$max_version")
[ -n "$release_notes" ] && args+=(-rn "$release_notes")

python3 "$tool" "${args[@]}" "$firmware" "$output"
echo "Created $output (vendor $vendor_id, product $product_id, version $version \"$version_string\")" >&2
python3 "$tool" show "$output"
