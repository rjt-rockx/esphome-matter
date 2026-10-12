#!/usr/bin/env bash
# Build the real esphome-matter firmware and boot it in Espressif's QEMU fork
# (ESP32 + OpenCores Ethernet). See docs/dev/qemu.md.
#
#   tools/qemu/run.sh build [yaml]      compile + merge a 4 MB flash image
#   tools/qemu/run.sh run   [yaml]      boot the image (builds it first if missing)
#   tools/qemu/run.sh test  [yaml]      boot, wait for "Server ready!", then exit 0/1
#   tools/qemu/run.sh cmd   [yaml]      print the QEMU command line (used by tests/runtime/conftest.py)
#
# Environment:
#   ESPHOME      esphome executable            (default: esphome from PATH)
#   QEMU_DIR     unpacked Espressif QEMU       (default: ~/tools/qemu/x/qemu, auto-downloaded)
#   QEMU_TAG     Espressif QEMU release tag    (default: 20260417)
#   BUILD_DIR    build root                    (default: ~/build-qemu)
#   MATTER_PORT  host UDP port -> guest 5540   (default: 5540)
#   API_PORT     host TCP port -> guest 6053   (default: 6053)
#   CMAKE_BUILD_PARALLEL_LEVEL compile jobs (default 3; cc1plus of CHIP needs ~1.5 GB each, OOM on small hosts)
#   BOOT_TIMEOUT seconds to wait in `test`     (default: 240)
#   WORK_NAME    build subdirectory name       (default: basename of the YAML; share one to rebuild incrementally)
#   FLASH_IMAGE  image `cmd` boots             (default: $BUILD_DIR/<name>/flash.run.bin)
#   FLASH_KEEP=1 keep the existing flash image (NVS/fabrics persist) on `run`
set -euo pipefail

MODE=${1:-run}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
YAML=${2:-$REPO/tests/qemu/esp32-openeth.yaml}
NAME=${WORK_NAME:-$(basename "$YAML" .yaml)}
STAGED=$(basename "$YAML" .yaml)
ESPHOME=${ESPHOME:-esphome}
BUILD_DIR=${BUILD_DIR:-$HOME/build-qemu}
QEMU_TAG=${QEMU_TAG:-20260417}
QEMU_DIR=${QEMU_DIR:-$HOME/tools/qemu/x/qemu}
MATTER_PORT=${MATTER_PORT:-5540}
API_PORT=${API_PORT:-6053}
BOOT_TIMEOUT=${BOOT_TIMEOUT:-240}
WORK=$BUILD_DIR/$NAME
IMAGE=$WORK/flash.bin
LOG=$WORK/uart.log

fetch_qemu() {
  [ -x "$QEMU_DIR/bin/qemu-system-xtensa" ] && return 0
  local base
  base=$(dirname "$QEMU_DIR")
  mkdir -p "$base"
  local f=qemu-xtensa-softmmu-esp_develop_9.2.2_${QEMU_TAG}-x86_64-linux-gnu.tar.xz
  echo "Downloading Espressif QEMU $QEMU_TAG" >&2
  curl -fsSL -o "$base/$f" "https://github.com/espressif/qemu/releases/download/esp-develop-9.2.2-$QEMU_TAG/$f"
  tar xf "$base/$f" -C "$base"
  if ldd "$QEMU_DIR/bin/qemu-system-xtensa" | grep -q "not found"; then
    echo "Missing shared libraries (try: apt-get install libslirp0 libglib2.0-0 libsdl2-2.0-0 libpixman-1-0):" >&2
    ldd "$QEMU_DIR/bin/qemu-system-xtensa" | grep "not found" >&2
    exit 1
  fi
}

build() {
  mkdir -p "$WORK"
  # Stage a copy: absolute external_components path, native ESP-IDF toolchain.
  sed -E "s#source: \.\./\.\./components#source: $REPO/components#" "$YAML" > "$WORK/$STAGED.yaml"
  grep -q '^  toolchain:' "$WORK/$STAGED.yaml" || \
    sed -i -E 's/^  framework:/  toolchain: esp-idf\n  framework:/' "$WORK/$STAGED.yaml"
  (cd "$WORK" && CMAKE_BUILD_PARALLEL_LEVEL=${CMAKE_BUILD_PARALLEL_LEVEL:-3} "$ESPHOME" compile "$STAGED.yaml")
  local b
  b=$(echo "$WORK"/.esphome/build/*/build)
  # Offsets come from the IDF-generated flasher_args.json, so partition layout changes are picked up.
  python3 - "$b" "$IMAGE" <<'EOF'
import json, subprocess, sys, os
b, out = sys.argv[1:3]
args = json.load(open(os.path.join(b, "flasher_args.json")))
files = [(off, os.path.join(b, f)) for off, f in args["flash_files"].items()]
cmd = ["esptool.py", "--chip", "esp32", "merge_bin", "--fill-flash-size", "4MB",
       "-o", out, "--flash_mode", "dio", "--flash_freq", "40m", "--flash_size", "4MB"]
for off, f in sorted(files, key=lambda x: int(x[0], 16)):
    cmd += [off, f]
subprocess.check_call(cmd)
EOF
  echo "Image: $IMAGE"
}

qemu_cmd() {
  # hostfwd: Matter (UDP 5540) and the ESPHome native API (TCP 6053). The guest gets
  # 10.0.2.15 (slirp DHCP). Only IPv4 is forwarded; the host needs no IPv6.
  QEMU_CMD=("$QEMU_DIR/bin/qemu-system-xtensa" -M esp32 -m 4M -nographic
    -drive "file=$IMAGE,if=mtd,format=raw"
    -nic "user,model=open_eth,hostfwd=udp::$MATTER_PORT-:5540,hostfwd=tcp::$API_PORT-:6053"
    -global driver=timer.esp32.timg,property=wdt_disable,value=true)
}

case "$MODE" in
  build) build ;;
  run|test)
    fetch_qemu
    [ -f "$IMAGE" ] || build
    qemu_cmd
    # QEMU modifies the image in place (NVS, fabrics): boot a copy so flash.bin stays pristine.
    [ "${FLASH_KEEP:-0}" = 1 ] && [ -f "$WORK/flash.run.bin" ] || cp "$IMAGE" "$WORK/flash.run.bin"
    QEMU_CMD=("${QEMU_CMD[@]/$IMAGE/$WORK/flash.run.bin}")
    if [ "$MODE" = run ]; then
      exec "${QEMU_CMD[@]}" 2>&1 | tee "$LOG"
    fi
    : > "$LOG"
    "${QEMU_CMD[@]}" > "$LOG" 2>&1 &
    PID=$!
    trap 'kill $PID 2>/dev/null || true' EXIT
    for _ in $(seq "$BOOT_TIMEOUT"); do
      if grep -q "Server ready!" "$LOG" 2>/dev/null; then echo "OK: Server ready!"; exit 0; fi
      kill -0 $PID 2>/dev/null || { echo "QEMU exited early"; tail -30 "$LOG"; exit 1; }
      sleep 1
    done
    echo "Timeout waiting for 'Server ready!'"; tail -40 "$LOG"; exit 1 ;;
  cmd)
    fetch_qemu
    IMAGE=${FLASH_IMAGE:-$WORK/flash.run.bin}
    qemu_cmd
    printf '%q ' "${QEMU_CMD[@]}"; echo ;;
  *) echo "usage: $0 build|run|test|cmd [yaml]" >&2; exit 2 ;;
esac
