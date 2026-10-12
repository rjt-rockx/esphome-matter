# Runtime tests

The runtime tests run the **real firmware** in Espressif's QEMU (see [qemu.md](qemu.md)) and drive it from two sides at
once, with pytest as the orchestrator:

* **Matter side:** the matter.js harness ([tools/harness](../../tools/harness/README.md)) commissions the device, reads and
  writes attributes, invokes commands and keeps a subscription, so attribute *reports* and *events* can be asserted.
* **ESPHome side:** `aioesphomeapi` on the forwarded port 6053 sets entity states (lights, switch, fan), reads them back and
  calls the `api:` actions that `tests/qemu/golden.yaml` defines to stimulate sensors and buttons.

This is the loop that finds the bugs unit tests and compile checks cannot: echo loops between Matter and ESPHome, lost
updates under bursts, refused commands that are not reconciled, event sequences.

```
tests/qemu/golden.yaml       one firmware with every entity type the tests need
tests/runtime/conftest.py    fixtures: qemu_device, device, matter, api, node
tests/runtime/test_smoke.py  boot, commission, toggle a light, read it back (every PR)
tests/runtime/test_golden.py the behavioural tests (nightly, label `runtime`)
```

## Running locally

Prerequisites: an ESPHome that supports `toolchain: esp-idf` (2026.9+), `aioesphomeapi` (installed with ESPHome),
`pytest`, Node 22 with `npm ci` run in `tools/harness`, and `libslirp0` + `libglib2.0-0` for QEMU (it is downloaded by
`tools/qemu/run.sh` on first use, or set `QEMU_DIR`).

```
cd tools/harness && npm ci && cd ../..
pytest tests/runtime -m smoke -v        # a few minutes once the image is built
pytest tests/runtime -v                 # smoke + golden, about 25 minutes (mostly subscription priming)
pytest tests/runtime -k reconcile -v    # one test
```

Run it from a virtualenv that has `esphome` and `esptool.py` (the fixtures put the interpreter's `bin` directory first on
`PATH` for `run.sh`). Ports 5540/udp and 6053/tcp on the host must be free: there is one device at a time.
Do not stop a stuck QEMU with `pkill -f qemu-system-xtensa` from a shell whose command line contains that text; use
`pkill -f "[q]emu-system"`.

The default `pytest` run (`pytest.ini` has `testpaths = tests/unit`) does not collect `tests/runtime`.

### Environment

| Variable | Default | Meaning |
|---|---|---|
| `RUNTIME_YAML` | `tests/qemu/golden.yaml` | firmware used by the `device` fixture |
| `RUNTIME_IMAGE` | | use this merged flash image, never build |
| `RUNTIME_CACHE` | `~/.cache/esphome-matter-runtime` | image cache (`<yaml>-<hash>/flash.bin`) |
| `RUNTIME_BUILD_DIR` | `~/build-qemu` | `BUILD_DIR` for `run.sh` |
| `RUNTIME_WORK_NAME` | `runtime` | `WORK_NAME` for `run.sh`: one shared ESPHome build tree, so changing the YAML is an incremental build (about 100 ninja steps instead of about 1500) |
| `RUNTIME_ARTIFACTS` | `test-artifacts/runtime` | `uart.log`, `harness.log`, `harness-commission.log`, `subscription.jsonl`, `build.log` |
| `BOOT_TIMEOUT` | `300` | seconds to wait for `Server ready!` |
| `CMAKE_BUILD_PARALLEL_LEVEL` | `3` | build jobs; `cc1plus` of CHIP needs about 1.5 GB each |

## Fixtures

* `qemu_device(yaml)` (session, factory): builds with `tools/qemu/run.sh build` unless an image for the *content hash* of the
  YAML, `components/` and `run.sh` is cached, copies the image (QEMU writes NVS into it), boots QEMU with the command
  line from `run.sh cmd`, waits for the `Server ready!` marker, tees the UART log to `uart.log`, and kills QEMU at the
  end. `device` is the default instance (`RUNTIME_YAML`). It offers `log_text()`, `log_mark()`, `log_lines(since,
  pattern)` and `wait_log(pattern, since, timeout)` (ANSI colors stripped).
* `matter` (session): commissions with the fixed manual code (`34970112332`; `matter.commission()` is a one-shot CLI call,
  `--ip 127.0.0.1 --port 5540 --operational-ip 127.0.0.1` because slirp does not forward mDNS), then talks to one
  long-running `cli.mjs serve` process: `read`, `write`, `invoke`, `subscribe`. matter.js locks its state directory, so a
  background `subscribe` next to one-shot commands is impossible; `serve` also keeps the CASE session warm (a one-shot
  command costs about 8 s under emulation, a `serve` request about a second).
  Subscription records are kept: `mark()`, `reports(ep, cluster, attribute, since)`, `events(...)`, `wait_report(...)`,
  `wait_event(...)`.
* `api` (session): `light()`, `switch()`, `fan()`, `action(name, **data)`, `action_burst(name, calls)`, `state(object_id)`,
  `wait_state(object_id, predicate, timeout)`.
* `node` (session): commissioned `matter` with a live subscription. Priming takes about 90 s under emulation and an
  attribute subscription only reports *changes*, so the fixture probes: it calls the `set_temp` API action until the
  resulting `MeasuredValue` report arrives.

## Writing a test

```python
def test_something(node, api, device):
    mark = node.mark()                                   # position in the subscription record stream
    node.invoke(1, "OnOff", "On")
    assert node.wait_report(1, "OnOff", "OnOff", True, since=mark, timeout=6)
    assert api.wait_state("golden_binary_light", lambda s: s.state is True, 6)
    assert not device.log_lines(device.log_mark(), r"Failed")   # UART assertions
```

Rules of thumb: take a `mark()` before the stimulus; leave the entity in a known state at the end (tests share one
device); assert on *reports* with a time budget, and on *quiet* (sleep, then check there was no flip-flop) for echo bugs.
Budgets are several times larger than on hardware because of CPU emulation.

## The golden firmware

`tests/qemu/golden.yaml` (ESP32 classic, OpenCores Ethernet, fixed passcode 20202021 and discriminator 3840):

| Endpoint | Device type | ESPHome side | Used by |
|---|---|---|---|
| 1 | on_off_light | binary light | light test |
| 2 | dimmable_light | monochromatic light (template float output, no transition, no gamma) | light test |
| 3 | on_off_plug_in_unit | template switch, not optimistic; `turn_on` is refused while the global `refuse_on` is set (`set_refuse` API action) | reconcile tests |
| 4 | temperature_sensor | template sensor, `set_temp` API action | sensor test, `node` fixture probe |
| 5 | generic_switch | template binary sensor, `press` API action (`short`, `double`, `long`) | switch events |
| 6 | fan | speed fan, `speed_count: 3`, template output | fan test |

Add an entity to the YAML when a new behaviour needs one; keep it buildable on ESP32 classic with `ethernet: OPENETH`.

## CI

`.github/workflows/runtime.yml`:

* every pull request that touches components, tests, tools or the workflow runs the **smoke** test (`pytest -m smoke`),
  with `continue-on-error` until it has proven stable;
* a pull request with the label **`runtime`**, the nightly schedule and manual dispatch run the whole directory
  (**golden** + smoke).

Caches: QEMU tarball, ESP-IDF tools (weekly), ccache, `managed_components`, and the merged firmware image by content hash.
The job uploads `test-artifacts/runtime/` (UART log, harness logs, subscription records, `junit.xml`) whether it passes or
not. Expect about 25 minutes cold and 6 to 10 minutes warm; the golden tests add about 15 minutes, most of it the one-time
subscription priming and the emulated crypto.

## Known failures

Tests that currently fail because of a real component bug are marked `xfail(strict=True)` with the reason, so the suite
stays green and the test turns into an error (XPASS) the moment the bug is fixed. Remove the marker then.
