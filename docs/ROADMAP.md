# Roadmap

This is a synthesis of what the community asks for, the direction of the upstream maintainer (DavidvtWout), the precedent for getting a protocol component into ESPHome, and the engineering gaps found in the code. It is a plan, not a promise: dates are deliberately absent. Facts below come from the project trackers and forums as of October 2026, mostly via summaries; check the linked threads before relying on exact counts or quotes.
Design direction and principles: [design/DIRECTION.md](design/DIRECTION.md).


## 1. What the community wants

- **Matter without Home Assistant.** The dominant theme in the long-running ESPHome feature requests ([#1430](https://github.com/esphome/feature-requests/issues/1430), about 124 thumbs-up; [#2722](https://github.com/esphome/feature-requests/issues/2722)) and the Home Assistant forum: cross-vendor interoperability, local control, no bridge, no single point of failure.
- **Matter over Wi-Fi first** to avoid buying new hardware; Thread for low-power devices second.
- **Entity coverage.** Switches/outlets, lights, fans, window coverings, thermostats, locks, valves and basic sensors; contact and presence sensors first for many.
- **Power and energy measurement.** The top hobbyist need. Implemented as the `electrical_sensor` device type with a selectable Power Topology (node, tree, set), see [sensors.md](sensors.md#electrical-measurements) and [power.md](power.md), but not yet verified on controllers.
- **Easy pairing in more ecosystems**, especially Google Home, and clear documentation.
- **Reliability.** Reconnect after restart, binding stability, fewer surprises in the config schema.

Caveats: evidence is thin and mostly from four places (the HA forum, the feature-request tracker, the upstream project tracker, one Lemmy thread). Reddit, YouTube and Hacker News could not be searched. Test reports exist for Home Assistant, Apple Home (Add Anyway) and one Alexa Echo; nothing was found for Google Home, SmartThings or IKEA beyond the maintainer's note.

Objections worth taking seriously: certification (test credentials need an uncertified-device flow in every ecosystem and may be restricted later), Matter being strict, and that Matter does not replace ESPHome's native API for Home Assistant.

## 2. Maintainer direction (DavidvtWout/esphome-matter)

From the upstream issues, pull requests and discussions, the maintainer's priorities appear to be:

1. **Matter and SDK currency.** Matter 1.5/1.6, tracking esp-matter 1.6.x ([#77](https://github.com/DavidvtWout/esphome-matter/issues/77)), a forked esp-matter.
2. **Binding and command reliability.** Binding fixes, command queue ([#32](https://github.com/DavidvtWout/esphome-matter/issues/32), PR [#64](https://github.com/DavidvtWout/esphome-matter/pull/64)), CASE-session-on-startup failures ([#31](https://github.com/DavidvtWout/esphome-matter/issues/31)), reconnect after restart.
3. **Credentials and certification-adjacent work.** Custom DAC support ([#5](https://github.com/DavidvtWout/esphome-matter/issues/5)), manufacturing tool ([PR #51](https://github.com/DavidvtWout/esphome-matter/pull/51)), documenting dev certificates ([discussion #44](https://github.com/DavidvtWout/esphome-matter/discussions/44)).
4. **Entity coverage.** Fans (fixes in [#90-#94](https://github.com/DavidvtWout/esphome-matter/issues/92)), covers incl. Venetian blinds (PR [#86](https://github.com/DavidvtWout/esphome-matter/pull/86)), climate as thermostat (PR [#53](https://github.com/DavidvtWout/esphome-matter/pull/53)), door lock ([discussion #74](https://github.com/DavidvtWout/esphome-matter/discussions/74)), more components ([#24](https://github.com/DavidvtWout/esphome-matter/issues/24), [discussion #25](https://github.com/DavidvtWout/esphome-matter/discussions/25)).
5. **Documentation and a first-release checklist** ([discussion #35](https://github.com/DavidvtWout/esphome-matter/discussions/35), [#47](https://github.com/DavidvtWout/esphome-matter/discussions/47)).
6. **Matter OTA** is explicitly "not a priority" ([#69](https://github.com/DavidvtWout/esphome-matter/issues/69)); BLE commissioning is acknowledged as broken and wanted, with no plan stated.

This fork follows upstream: changes that do not conflict are intended to flow back, and we try to keep our edits small, additive and in new files.

## 3. Upstreaming into ESPHome: the Zigbee precedent

No ESPHome maintainer has publicly said yes or no to a Matter component. What exists is a precedent and the substrate:

- **Zigbee** ([esphome/esphome PR #11553](https://github.com/esphome/esphome/pull/11553)) went from luar123's external component `zigbee_esphome` to core, merged on 2026-04-23 (shipped in ESPHome 2026.5.0) after about 150 commits. It started small (ESP32-C6/H2, ESP-IDF only, binary sensors) and added sensors and triggers in follow-ups. Prerequisites were split into separate PRs. The PR was flagged as too big by the review bot and merged anyway.
- **Substrate already in ESPHome:** an OpenThread component (2025.6), native ESP-IDF toolchain (2026.5), `network: enable_ipv6`, and mbedTLS trimming with `require_mbedtls_*` hooks (the Matter component must opt in to the TLS-server/crypto features it needs).
- **Likely terms (inference, not a statement from maintainers):**
  1. ESP-IDF only, a clearly scoped MVP, and a working external component first (this repo).
  2. A series of small PRs: prerequisites (network/IPv6, mbedTLS gating), then the component.
  3. Footprint is scrutinized: report flash and RAM numbers up front.
  4. Depend on upstream `espressif__esp_matter` rather than a fork (the upstream project currently uses a fork, which would need to be resolved).
  5. Documentation in esphome-docs and tests that fit ESPHome's CI.
- The first useful step is to reach the maintainers in `#devs` / by discussion with a small proposal and measurements, as Hedda suggested in [discussion #37](https://github.com/DavidvtWout/esphome-matter/discussions/37).

## 4. Prioritized roadmap

Priorities weigh community demand, maintainer direction, effort, and risk. P0 items affect trust and correctness; later items extend coverage.

### P0: correctness, trust and first-run experience

- **Honest documentation.** Keep the docs accurate (device types, README, ecosystems and troubleshooting were refreshed together with this roadmap). Keep a tested/untested matrix.
- **Verification on hardware.** Run the same test matrix against Home Assistant, Apple Home, Alexa and Google Home for lights, sensors and binary sensors; record results in [ecosystems.md](ecosystems.md).
- **Reboot persistence test** for attribute values (StartUpOnOff, level, etc.), since correctness depends on NVS persistence.
- **Pairing UX and secret handling.** Done: the passcode is stored in flash only until the first commissioning (`persist_passcode: until_commissioned`, default) and afterwards only the SPAKE2+ verifier is kept; the setup code is logged only while a commissioning window is open (see [commissioning](commissioning.md#passcode-storage)). Remaining: a diagnostic text sensor / `qr_code` display for the code, and offering both "remove fabrics" and "regenerate identity" resets as separate UX.
- **Reconnect after restart** ([#8](https://github.com/DavidvtWout/esphome-matter/issues/8)/[#31](https://github.com/DavidvtWout/esphome-matter/issues/31)): reproduce, measure, fix with the command queue and startup ordering.
- **Compatibility with the ESPHome `api` component** and duplicate entities in Home Assistant: document "one path per entity" and measure heap with both.

### P1: entity coverage (high demand)

- **Switch** to On/Off Plug-in Unit ([switch.md](switch.md)).
- **Binary sensors** driven by `device_class` ([binary-sensors.md](binary-sensors.md)): contact, occupancy, water leak, rain, smoke/CO.
- **Sensors** correctness and completeness ([sensors.md](sensors.md)): unit conversion, concentration units, battery (Power Source) and `device_class` validation are implemented but unverified on controllers.
- **Buttons / events as Generic Switch** (press, double press, long press).
- **Cover** ([cover.md](cover.md)), **fan** ([fan.md](fan.md)), **lock and valve** ([lock-valve.md](lock-valve.md)), **climate** to Thermostat.
- **Identify** (`on_identify` trigger) so ecosystems can make a device blink during pairing.

### P2: power, identity and operations

- **Electrical sensor**: power, voltage, current, energy (top hobbyist need). Implemented, see [sensors.md](sensors.md#electrical-measurements); controller support still to be verified. Periodic energy and energy reset are not implemented.
- **Basic Information options** (node label, serial number), configurable reporting intervals, Power Source for batteries.
- **Light polish**: hue/saturation commands, honoring transition times.
- **Footprint options**: expose the CHIP shell, endpoint count and optional LTO as release settings; offer a custom partition table for 4 MB boards.
- **CI**: compile jobs for each variant (ESP32 classic is not validated), ccache persistence, managed-component cache.

### P3: bigger bets

- **BLE commissioning** (needs coexistence with other networking; currently broken).
- **Matter OTA**: an opt-in requestor is implemented ([matter-ota.md](matter-ota.md)); ESPHome OTA remains the recommended update path.
- **Custom DAC / production credentials** workflow and CSA VID guidance.
- **Ethernet** transport.
- **Matter 1.4/1.5 types**: solar, battery storage, heat pump, EVSE, closures, cameras. Mostly untestable by hobbyists today.
- **Upstreaming**: submit prerequisites to ESPHome, then a minimal component PR (see section 3).

### Not planned / no good mapping

ESPHome `number`, `text_sensor` and `alarm_control_panel` have no good Matter device type. `media_player` and `select` have weak, partly ecosystem-dependent mappings. Sleepy end devices on Wi-Fi are not possible in esp-matter 1.6 (ICD is Thread-only).

## 5. How to influence the roadmap

Report test results and open issues. Reports of "works / does not work" in a specific ecosystem are the most valuable contribution right now. See [ecosystems.md](ecosystems.md#what-to-report).
