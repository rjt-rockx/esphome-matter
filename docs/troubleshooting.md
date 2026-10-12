# Troubleshooting

Common problems with esphome-matter, grouped by symptom. For ecosystem-specific pairing steps see [ecosystems.md](ecosystems.md). Items marked "not measured" or "unverified" are based on reading source code or reports rather than on measurements.

## Commissioning fails

Check these in order:

1. **Window closed.** The commissioning window is open for 15 minutes after boot, and only if the device has not joined any fabric. Restart the device to reopen it. If the device already belongs to a fabric, the window is not opened on restart; use "Share" in the owning ecosystem to get a temporary code (see [ecosystems.md](ecosystems.md#multi-admin-sharing-a-device-with-more-than-one-ecosystem)). A report of the window staying closed after reboot and reinstall exists upstream ([issue #61](https://github.com/DavidvtWout/esphome-matter/issues/61)); a Matter factory reset (`matter.factory_reset`) reopens it.
2. **Certificate rejection.** Home Assistant (matterjs-server) refuses test credentials unless test-net DCL is enabled. Google Home needs a developer console project. Apple needs "Add Anyway".
3. **Use the log.** The log prints the QR code, QR URL and manual pairing code whenever a commissioning window is open (also from `dump_config`), which includes every boot while the device is not commissioned (the code is the same each time, see [commissioning](commissioning.md#passcode-storage)), plus the state of the window and the number of fabrics on every boot. The setup code is a secret: do not paste it in public reports.
4. **Network.** The controller must reach the device over IPv6 (see below).

## IPv6, mDNS and networking

Matter discovers devices with mDNS/DNS-SD and talks to them over IPv6 (the Matter protocol also needs IPv6 link-local or ULA addressing). Most "No response" and "Unable to connect" errors are network problems.

- **Enable IPv6.** Your YAML needs:

  ```yaml
  network:
    enable_ipv6: true
  ```

- **Same network segment.** The controller (phone, hub, Home Assistant) and the device should be on the same layer-2 network and VLAN. mDNS multicast does not cross VLANs or subnets unless you run an mDNS reflector/repeater.
- **Router and access point settings.** Features such as client isolation / AP isolation, IGMP snooping without a querier, and blocked multicast (including mDNS on port 5353 and IPv6 multicast) break discovery. If Apple Home says "No Response", check these first.
- **IPv6 address types.** On VLAN setups, having only link-local IPv6 can be insufficient; a ULA or global prefix on the network helps. Disabling IPv6 on the router or blocking router advertisements will break Matter over Wi-Fi.
- **Firewall.** Allow UDP 5540 (Matter) between controllers and the device, and mDNS (UDP 5353).
- **Home Assistant in a container or VM.** The Matter server needs host networking (or equivalent mDNS and IPv6 access to the LAN), otherwise commissioning "existing device" fails to find the node.
- **Test with a tool.** `avahi-browse -r _matter._tcp` and `avahi-browse -r _matterc._udp` (or `mdns-scanner`) from a machine on the same network will show whether the device advertises. `_matterc._udp` appears for an uncommissioned device; `_matter._tcp` for a commissioned one.
- **Ethernet.** Matter over Ethernet is not supported yet.
- **ESPHome network components.** Thread and Wi-Fi coexistence is not implemented in ESPHome's OpenThread component.

## Slow reconnect after restart

After a restart the device may take up to about a minute before controllers can talk to it again. Known upstream issues in this area: [#8](https://github.com/DavidvtWout/esphome-matter/issues/8) (closed) and [#31](https://github.com/DavidvtWout/esphome-matter/issues/31) (CASE session fails on startup, open). The causes are the time needed to join Wi-Fi/Thread, re-advertise over mDNS, and for controllers to retry; controllers' retry back-off differs and is outside the device's control.

What helps:

- Make sure Wi-Fi or Thread connects quickly (good signal, fixed channel/BSSID where sensible).
- Check the log for when `Fabrics:` shows your fabrics and the network is up.
- Binding commands (device to device) can fail for several seconds after boot until sessions are re-established. A command queue is in development ([PR #64](https://github.com/DavidvtWout/esphome-matter/pull/64)).
- Wait a minute before assuming the device is broken, before factory resetting it.

## Compile time and disk space

esphome-matter pulls in the esp-matter SDK and connectedhomeip (as a managed ESP-IDF component) and compiles a large amount of C++.

- The first build is slow and the build directory is large. Exact build times have not been measured; expect a cold build to take much longer than a typical ESPHome build, and several GB of disk per target (estimate). Keep at least ~10 GB free for the first build.
- The full connectedhomeip source tree is several hundred MB (about 340 MB in the esp-matter 1.6 source repository); ESPHome fetches a reduced component, but caches, toolchains and per-target builds add up.
- Incremental builds are much faster than the first build. Do not delete the build directory (`.esphome/build/<name>`) between builds.
- If `ccache` is installed, ESPHome picks it up automatically, which helps a lot for rebuilds and for CI. Persist its cache directory between CI runs.
- Run the build on a machine with enough RAM (a Raspberry Pi or a small Home Assistant add-on host can be very slow, or run out of memory/disk). Prefer building on a desktop and using ESPHome OTA to upload.
- In the Home Assistant ESPHome add-on, make sure the data disk has enough free space.

## Flash size, especially on 4 MB boards

- A Matter build is large: on the order of 1.3 to 1.5 MB for a basic Wi-Fi light on a C3/C6 (esp-matter reference figures; your numbers depend on the endpoints you add). Check the "Used Flash" report at the end of the build.
- On a **4 MB** flash, ESPHome's automatic partition table gives two application slots of 1.75 MB each (needed for OTA), so there is room for a small number of endpoints, but little spare headroom. Adding many endpoints or other large components can overflow a slot and the build fails with "app partition is too small" or similar.
- **8 MB** boards (or larger) give ample room (3.75 MB per slot).
- Ways to save flash: enable only the endpoints/clusters you need (esphome-matter only compiles the clusters that you configure), remove components you do not use, and avoid BLE and unneeded networking features. Disabling the CHIP shell and similar sdkconfig tweaks can save tens of kB on ESP32-C3 according to the esp-matter documentation, but these are not exposed as YAML options yet.
- If you use a custom partition table, keep two equally sized app slots (for OTA), an NVS partition large enough for fabric data, and the otadata partition.
- ESP32 (classic) is Wi-Fi only (no 802.15.4 radio) and is not validated in CI. ESP32-C3, C5, C6, S3 and H2 have been reported to work.

## Thread

Matter-over-Thread requires the `openthread` component, ESPHome 2026.6.0 or newer, a chip with an IEEE 802.15.4 radio (ESP32-C5, C6, H2), and a Thread border router on your network.

- **A Thread border router is required** for the controller to reach the device (for example the Home Assistant OpenThread Border Router app with a Connect ZBT-1/ZBT-2, an Apple HomePod mini/Apple TV, a Google Nest hub, or an Espressif ZeroCode). The device must be on the same Thread network as the border router: set the same dataset in `openthread:` (`tlv:`).
- **Thread is not Matter.** ESPHome's own Thread support alone uses the ESPHome native protocol over Thread. You need the `matter:` block (and a Matter controller) for Matter over Thread. A board that "joins my Thread mesh" with only `openthread:` is not proof that Matter works.
- **Border router and fabrics.** Apple border routers can block mDNS traffic needed for Thread OTA; this does not matter for esphome-matter because OTA is done over ESPHome.
- **Known issue.** Because ESPHome initializes the OpenThread stack, the Matter network name can show up wrong (garbage) in some controllers. The real fix needs support in connectedhomeip for an externally managed OpenThread stack.
- **Debugging.** See [dev/thread-debugging.md](dev/thread-debugging.md) for `ot-ctl` and mDNS commands.
- **Sleepy end devices.** Not supported in ESPHome's OpenThread component at the time of writing. Do not assume battery-friendly Thread behaviour.
- Wi-Fi/Thread/BLE coexistence is not implemented.

## Setup code and reset

- By default the passcode is stored in flash only until the first commissioning (`persist_passcode: until_commissioned`), afterwards only the SPAKE2+ verifier. The setup code is shown in the log while a commissioning window is open. See [commissioning](commissioning.md#passcode-storage).
- `matter.factory_reset` wipes fabrics and reopens the commissioning window. The discriminator is kept, and the passcode is new unless `persist_passcode: true` or `passcode:` is set. `matter.reset_identity` also creates a new discriminator.
- Fabric data is stored in flash (NVS) and survives OTA updates.

## Other known issues

- **BLE commissioning is currently broken**, and `esp32_ble*` components cannot be used in the same YAML.
- **Config schema may still change** and break existing configurations between versions.
- **Device shows `TEST_VERSION` / test vendor** in your app: set `vendor_name`, `product_name`, `hardware_version_string` in `matter:`.
- **Controller shows the wrong unit** for an air-quality or pressure sensor: see [device-types.md](device-types.md#sensors).

## Reporting a problem

Open an issue with: ESPHome and esphome-matter versions, ESP32 variant, Wi-Fi or Thread, the `matter:` and relevant entity YAML, the controller (name and version), and log output from boot to the failure. Remove the setup code from any posted log.
