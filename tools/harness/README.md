# esphome-matter test harness

A scriptable Matter **controller** for exercising esphome-matter devices (real or emulated) from a terminal or from tests,
without a phone or Home Assistant. It is plain ESM JavaScript on Node 22+ and [matter.js](https://github.com/project-chip/matter.js)
(pinned to `0.17.9`, see `package.json`; use `npm ci`).

```
cd tools/harness
npm ci
node src/cli.mjs selftest            # proves the harness works on this machine, no hardware needed
```

State (controller fabric, commissioned nodes) lives in `./.harness-state` (override with `--state <dir>` or
`$HARNESS_STATE`). `node_modules` and the state dirs are git-ignored. stdout carries only machine readable output
(JSON); logs go to stderr (`--verbose [debug|info|notice|warn]` shows matter.js logs).

## Commands

| Command | Purpose |
|---|---|
| `commission --code <manual code \| MT:...> [--ip <addr> [--port 5540]] [--node-id N] [--timeout 120]` | On-network commissioning. With `--ip` the device is contacted directly at that address (no discovery needed for PASE); without it the device is found via mDNS using the discriminator from the code. Prints `{nodeId, elapsedMs}`. |
| `list` | Commissioned node ids. |
| `dump --node <id> [--out file.json]` | Wildcard read of everything, incl. global attributes (FeatureMap 0xFFFC, AttributeList 0xFFFB, AcceptedCommandList 0xFFF9, GeneratedCommandList 0xFFF8, EventList 0xFFFA, ClusterRevision 0xFFFD). |
| `read --node --ep --cluster --attribute` | One attribute. Cluster/attribute accept names (`OnOff`, `CurrentLevel`, case-insensitive) or ids (`6`, `0x0006`). |
| `write --node --ep --cluster --attribute --value <json>` | Write an attribute, e.g. `--value 2100`, `--value '"label"'`, `--value '{"a":1}'`. |
| `invoke --node --ep --cluster --command [--args <json>]` | Invoke a command, e.g. `--command MoveToLevel --args '{"level":128,"transitionTime":0,"optionsMask":{},"optionsOverride":{}}'`. Bitmaps are objects of booleans, enums numbers, octet strings `"hex:0a0b"`, 64 bit ints `{"$big":"123"}`. |
| `subscribe --node [--duration 30] [--min 0] [--max 60] [--out file.jsonl]` | Subscribes to all attributes and events and prints one JSON line per attribute report / event / connection state change, with ISO timestamp and `dtMs` since start. Use it to time reconnects and subscription resumption (see below). |
| `remove --node <id>` | Decommission (RemoveFabric on the device, then forget it locally). |
| `selftest [--transport sim\|real] [--restart]` | End-to-end self check, see below. |
| `scenario <files...> [--node] [--var ep=2,...] [--junit out.xml] [--code/--ip/--port --keep]` | Declarative test scripts. |

`node` may be omitted when exactly one node is commissioned.

### Dump format

```json
{
  "nodeId": 1,
  "data":  { "1": { "0x0006": { "0x0000": false, "0xFFFC": {"lighting": false, ...}, "0xFFFB": [0, 16384, ...] } } },
  "names": { "clusters": {"0x0006": "OnOff"}, "attributes": {"0x0006": {"0x0000": "OnOff"}} }
}
```

`data` is `{endpoint: {clusterId: {attributeId: value}}}` with hex ids, all keys sorted, so two dumps can be diffed
(`diff <(jq -S . a.json) <(jq -S . b.json)`). Octet strings are `"hex:..."`, 64 bit values beyond 2^53 are decimal
strings. `names` maps the ids to standard names.

## Scenarios

YAML (several `---` documents per file) or JSON. Example (`scenarios/light.yaml`):

```yaml
name: light on/off
vars: { ep: 1 }                           # overridable: --var ep=2
requires: [{ ep: "${ep}", cluster: OnOff }]   # scenario is SKIPPED (not failed) if the device lacks it
setup:                                    # same step syntax, run first
  - invoke: { ep: "${ep}", cluster: OnOff, command: Off }
steps:
  - invoke: { ep: "${ep}", cluster: OnOff, command: Toggle }
  - expect: { ep: "${ep}", cluster: OnOff, attribute: OnOff, equals: true, via: report, within: 2s }
cleanup:                                  # always runs
  - invoke: { ep: "${ep}", cluster: OnOff, command: Off }
```

Steps:

| Step | Keys |
|---|---|
| `invoke` | `ep cluster command args? expectError? response?` (`expectError: true` or a substring of the expected error message; `response` takes an expectation block applied to the command response) |
| `write` | `ep cluster attribute value expectError?` |
| `read` | `ep cluster attribute` + expectation, `save: varname` stores the value |
| `expect` | `ep cluster attribute` + expectation, `within: 2s` (default 2s), `via: report` (require an attribute *report* from the subscription seen since the last invoke/write) or `via: read` (poll only). Default: poll with reads and also wake on reports. |
| `expect_event` | `ep cluster event within? data?` waits for an event from the subscription |
| `sleep` / `log` / `set_var` | `sleep: 500ms` |

Expectation keys: `equals notEquals oneOf gt gte lt lte contains exists hasBits`, and `path: a.b.0` to look inside
struct/list values. Durations: `500ms`, `2s`, `1m` or a number of ms. `${var}` is substituted everywhere (a whole-string
`"${ep}"` keeps the variable's type). `subscribe: false` on a scenario skips the live subscription. Exit code is 1 if any
scenario fails; `--junit` writes a JUnit file.

Examples in `scenarios/`: `light.yaml` (on/off, level, colour temperature), `switch.yaml`, `fan.yaml`, `cover.yaml`,
`thermostat.yaml`, `lock.yaml`, `sensors.yaml` (temperature, humidity, occupancy, contact, illuminance, pressure) and
`virtual-device.yaml` (runs against the built-in virtual device; used to validate the runner). Run against a device
whose endpoint layout differs with `--var ep=N`. The hardware ones (fan, cover, thermostat, lock, sensors) have only been
checked for syntax/name resolution, not against a device yet; value choices (e.g. the lock's `LockState` numbers) follow
the Matter spec and docs/*.md.

## Selftest and the virtual device

`src/virtual-device.mjs` is a matter.js device (On/Off light on endpoint 1, temperature sensor on endpoint 2) that can also be run
standalone (`node src/virtual-device.mjs --storage /tmp/vd --port 5541`; prints a JSON `ready` line with the pairing codes; stdin
accepts `onoff 0|1`, `temp <centi-C>`, `quit`).

`selftest` runs it and checks: commission, dump (endpoints, global attributes), read, subscribe, `OnOff.Toggle` invoke -> attribute
report arrives, device-side change -> report, write + read back, controller restart from persisted state, decommission.
`--restart` additionally reboots the device mid-subscription and prints the resumption time.

* `--transport sim` (default when the host has no IPv6): device and controller run in one process on matter.js' in-memory
  `NetworkSimulator`. Exercises the whole Matter stack (mDNS, PASE, CASE, IM) but not the OS sockets.
* `--transport real` (default when IPv6 is available): the device is a child process, the controller talks to it over real UDP
  (`SELFTEST_IP=<addr>` picks the address, `SELFTEST_MDNS=1` uses mDNS discovery instead of a known address).

## Networking: what works where

* matter.js unconditionally creates **UDP/IPv6** sockets (mDNS on `[::]:5353`, operational port) and aborts if the kernel refuses
  (`EAFNOSUPPORT`). Matter itself is IPv6 first: mDNS answers of real devices carry AAAA records and (link-local) addresses.
* On a normal host/LAN with IPv6 nothing special is needed. For a real ESP32 on Wi-Fi use either mDNS discovery
  (`commission --code ...`, controller and device on the same L2 segment) or the device's address:
  `--ip 192.168.1.50` (IPv4 works if the device has an IPv4 address and matter.js picks that route) or a link-local IPv6 with zone
  `--ip fe80::1234%eth0`. Multicast must pass between you and the device (no client isolation, IGMP/MLD snooping not blocking).
* **This build container has no IPv6 at all** (no `/proc/net/if_inet6`, `socket(AF_INET6)` -> EAFNOSUPPORT; only `lo` and
  `eth0` with IPv4 192.0.2.2). The harness therefore installs `src/ipv6-shim.mjs` automatically when the kernel lacks IPv6 (or when
  `HARNESS_IPV6_SHIM=1`): `udp6` sockets are replaced by inert stubs so matter.js starts and runs purely over its IPv4 sockets. With
  the shim, commissioning, mDNS discovery, CASE, reads, invokes, subscriptions all work over real UDP/IPv4 between processes in
  this container (verified, see selftest `--transport real`). Limitation: only IPv4-reachable peers work, and the device must
  advertise an A record (matter.js devices and esp-matter Wi-Fi/Ethernet devices with IPv4 do).
* BLE commissioning, Thread/Wi-Fi credential provisioning and off-network (cloud) paths are not part of this harness; devices must be
  commissioned on-network (already on the LAN and in commissioning mode, or with an open commissioning window).

## Notes for esphome-matter devices

* **Attestation**: esp-matter test/development DACs are signed by the CHIP test PAA. The matter.js controller used here has no
  DCL/PAA trust store configured, so it logs attestation findings as warnings ("accepted for backward compatibility") and carries
  on. That makes the harness suitable for development builds (but it is not a certification check).
* **Commissioning window**: a device that is already commissioned is not in commissioning mode. Either factory reset it or open a
  window from the first controller (Home Assistant, chip-tool `pairing open-commissioning-window`) and use the printed manual code.
* **Subscription resumption**: `subscribe` is the tool for measuring reconnect behaviour. Reboot the device while it runs and compare
  the `state`/`attribute` lines. In the `--restart` selftest (matter.js device, loopback) the matter.js controller needed about a
  minute to notice the reboot and resume the subscription, even with a 5 s max interval: after a `ShutDown` event matter.js hands
  reconnection to its `SustainedSubscription`, which only retries after the subscription timeout. Do not assume instant recovery
  when writing test timeouts for reboot scenarios.
* The `state` names in the subscribe output come from matter.js' `PairedNode` (`reconnecting`, `connected`, `disconnected`,
  `structureChanged`, ...); a `connected` line right after `reconnecting` is not proof that the device is reachable again. Use
  attribute reports as the ground truth.
* The wildcard dump uses `isFabricFiltered: false`. matter.js' own virtual device does not serve EventList (0xFFFA); esp-matter devices do.

## Layout

```
src/cli.mjs            argument handling / commands
src/controller.mjs     controller wrapper (commission, dump, read, write, invoke, subscribe, remove)
src/model.mjs          cluster / attribute / command name resolution
src/scenario.mjs       scenario runner
src/selftest.mjs       end-to-end self check
src/virtual-device.mjs matter.js virtual light + temperature sensor
src/ipv6-shim.mjs      IPv6-less host workaround
scenarios/             example scenarios
```
