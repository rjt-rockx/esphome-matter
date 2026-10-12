# Running the real firmware in QEMU

`tools/qemu/run.sh` builds the real esphome-matter firmware for an ESP32 and boots it in
[Espressif's QEMU fork](https://github.com/espressif/qemu/releases), which emulates the ESP32 and
an OpenCores Ethernet MAC. The emulated device can then be commissioned and controlled over
Matter from the same machine, with no hardware, Wi-Fi or BLE.

```
tools/qemu/run.sh build            # esphome compile + merge a 4 MB flash image (long: ~1500 ninja steps)
tools/qemu/run.sh run              # boot interactively, UART log on stdout and in $BUILD_DIR/<name>/uart.log
tools/qemu/run.sh test             # boot, exit 0 once CHIP logs "Server ready!"
```

The test firmware is `tests/qemu/esp32-openeth.yaml`: ESP32 (classic), ESP-IDF, `ethernet: type: OPENETH`,
an on/off light on endpoint 1 and a template temperature sensor on endpoint 2. The passcode
(`20202021`, manual code `34970112332`) and discriminator (`3840`) are fixed.

`run.sh` downloads the QEMU release (`QEMU_TAG`, default `20260417`) into `~/tools/qemu` on first use and
needs `libslirp0` and `libglib2.0-0`. It compiles with `toolchain: esp-idf` (the staged YAML gets that line
added), takes flash offsets from the IDF `flasher_args.json`, and boots with

```
qemu-system-xtensa -M esp32 -m 4M -nographic -drive file=flash.run.bin,if=mtd,format=raw \
  -nic user,model=open_eth,hostfwd=udp::5540-:5540,hostfwd=tcp::6053-:6053 \
  -global driver=timer.esp32.timg,property=wdt_disable,value=true
```

The image is copied to `flash.run.bin` before every boot (NVS and fabrics live in that copy); `FLASH_KEEP=1`
reuses the copy so a commissioned device survives a restart.

## What to expect

UART log of a boot (colors stripped):

```
[I][matter.event:099][CHIP]: Server ready!
[W][component:290]: ethernet set Warning flag: waiting for IP configuration
[I][matter.commissioning:344]: Commissioning window open. SetupQRCode: MT:...
[I][matter.commissioning:351]: Manual pairing code: 34970112332
[D][matter.event:047][CHIP]: Interface IP Address changed
[D][matter.dnssd:295][CHIP]: Publishing ..._matterc._udp as 000000000003:5540
```

The guest gets 10.0.2.15 from slirp's DHCP (the ESPHome config dump may still print `0.0.0.0` because it runs
before the lease). Boot to "Server ready!" takes about a minute of wall time.

## Commissioning from the host

QEMU user networking is NAT: nothing multicast crosses it, so DNS-SD does not work. Use the matter.js harness
(`tools/harness`, `npm ci` first) with the forwarded port:

```
node src/cli.mjs commission --code 34970112332 --ip 127.0.0.1 --port 5540 --operational-ip 127.0.0.1 --timeout 300
node src/cli.mjs dump
node src/cli.mjs invoke --ep 1 --cluster OnOff --command Toggle
node src/cli.mjs read --ep 2 --cluster TemperatureMeasurement --attribute MeasuredValue
```

* `--ip/--port` makes PASE go straight to the forwarded UDP port.
* Without `--operational-ip` commissioning fails at step 18 (CASE reconnect: "peer unreachable"), because the
  controller looks the node up by its operational DNS-SD name. `--operational-ip` (or
  `HARNESS_OPERATIONAL_IP`) pins that address as the fallback. Pass it on every later command too.
* Only IPv4 is used (the container has no IPv6); the harness installs its IPv6 shim automatically.
* Subscriptions work but the priming report takes about 90 s under emulation; use `subscribe --duration 150`.

## Troubleshooting

* `cc1plus: Killed` during `build`: out of memory. Lower `CMAKE_BUILD_PARALLEL_LEVEL` (default 3); the build resumes.
* QEMU exits early / no output: check `ldd qemu-system-xtensa` for missing libraries.
* Do not run `pkill -f qemu-system-xtensa` from a shell whose own command line contains that string.
