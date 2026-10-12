# Ecosystem pairing guide

How to add an esphome-matter device to each smart home ecosystem, what to expect, and known quirks. For the general commissioning flow and the setup code printed on boot, see [commissioning.md](commissioning.md).

By default esphome-matter uses the Matter **test** vendor ID `0xFFF1` and product ID `0x8000` with a development attestation certificate (DAC). The device is not Matter-certified, and every ecosystem has its own way of dealing with that. Ecosystems that accept it today may become stricter later.

How to read the status column below:

| Status | Meaning |
|---|---|
| Confirmed | A user or maintainer reported a successful pairing in the project tracker or discussions. |
| Documented | Described in this repo's docs or in vendor docs, but not independently confirmed for esphome-matter. |
| Untested | No report found. Please test and [report](#what-to-report). |

| Ecosystem | Status | Special steps needed |
|---|---|---|
| Home Assistant (matterjs-server / Matter Server app) | Confirmed (maintainer) | Enable test-net DCL, use "Commission existing device" |
| Apple Home | Confirmed (user report) | Accept the "Uncertified Accessory" warning with "Add Anyway" |
| Amazon Alexa | Confirmed (Echo Gen 4, Wi-Fi, a light on/off; self-reported) | None reported |
| IKEA Home smart (DIRIGERA) | Confirmed (maintainer) | None, but only a limited set of device types is shown |
| Google Home | Untested | Test vendor ID needs a Google Developer Console project |
| Samsung SmartThings | Untested | Unknown |

Keep in mind that a controller *accepting* a device does not mean it supports every device type properly. Several ecosystems only show a subset of Matter device types (see the per-domain notes in the [README feature matrix](../README.md#feature-matrix)).

---

## Home Assistant

Home Assistant's Matter integration is the best-supported path, and the one most people will use to get the full device (all endpoints and clusters).

Since the Matter Server was rewritten as **matterjs-server** (the Matter Server app 9.0, June 2026), devices that carry official development/test certificates can no longer be added out of the box. The server checks certificate revocation data during commissioning and needs to be told to accept test-net credentials.

**Matter Server app (Home Assistant OS):**

1. Go to Settings -> Apps -> Matter Server -> Configuration.
2. Enable **Enable test-net DCL usage** and restart the app.
3. If you skip this, pairing will fail.

**Standalone matterjs-server:** start it with `--enable-test-net-dcl=true`, or set the environment variable `ENABLE_TEST_NET_DCL=true`:

```sh
matterjs-server --enable-test-net-dcl=true
```

**Commissioning:**

1. In the Home Assistant companion app or the web UI, add a Matter device.
2. When asked whether the device is new, choose that it is **already in use** (the "Commission existing device" option). ESPHome devices already have their Wi-Fi or Thread credentials from the YAML, so there is nothing to provision over BLE.
3. Scan the QR code, or enter the manual pairing code, from the device log (see [commissioning.md](commissioning.md)).

![matterjs-server commission dialog](img/matterjs-server-commission.png)

Notes:

- python-matter-server accepted the dev DAC without extra steps, but it is discontinued and archived. Migrate to matterjs-server.
- Matter-over-Thread devices need a Thread border router that Home Assistant can reach (for example the OpenThread Border Router app). See [troubleshooting.md](troubleshooting.md#thread).
- The same device can also be added through the ESPHome integration via the native API (if `api:` is configured). Home Assistant will then show it twice. Pick one path per entity to avoid duplicates, unless you intentionally want both. Running both at once is untested for duplicate entity behaviour.
- Home Assistant shows update entities only for devices that support Matter OTA, which esphome-matter does not. Update the firmware over ESPHome's own OTA.

## Apple Home

Apple Home accepts the device with an extra confirmation:

1. Open the Home app, tap **+** -> **Add Accessory**, and scan the QR code (or tap "More options" and enter the manual code).
2. Apple shows a warning that this is an **Uncertified Accessory**. Tap **Add Anyway**.
3. Apple displays the test manufacturer and model strings (set `vendor_name`, `product_name` and the other `matter:` options to change them; the vendor and product IDs still identify the device as a test device).

Notes:

- You need a home hub (HomePod or Apple TV) for remote access and automations.
- Apple's mDNS handling is strict. If Apple says "No Response" or "Unable to connect", see [troubleshooting.md](troubleshooting.md#ipv6-mdns-and-networking).
- Apple supports a subset of Matter device types. Appliances and some newer types are missing in some iOS versions; for unsupported types the accessory may not appear. Support for types such as water valves or thermostat variants is unverified.
- A Matter-over-Thread device needs an Apple Thread border router (HomePod mini, Apple TV 4K with Thread). Reports of an ESP32-H2 joining an Apple Thread mesh refer to ESPHome's own protocol over Thread, not Matter.

## Google Home

Google does not automatically accept devices with the Matter test vendor ID. Devices using a test VID/PID (`0xFFF1`-`0xFFF4`) must be registered in a **Google Developer Console** project before the Google Home app will commission them. This is a Google requirement, not an esphome-matter limitation.

The approach, as described in Google's developer documentation and followed by the [Tasmota guide](https://tasmota.github.io/docs/Matter-with-Google/) (which is the reference this project points to; the steps below are a summary and have **not** been verified end to end with esphome-matter):

1. Create a project in the Google Home Developer Console (console.home.google.com).
2. Add a Matter integration to the project.
3. Enter the test **vendor ID `0xFFF1`** and **product ID `0x8000`** (the esphome-matter defaults; use your values if you changed them in the `matter:` block). Pick a device type that matches your endpoint and give it a name.
4. Use the Google Home app **with the same Google account** that owns the console project, and add the device with the QR or manual code.
5. Google may warn that the device is not certified, and may reduce what it exposes.

Notes:

- Google Home Android commissioning may fail without a country set on the phone; using an iPhone is a reported workaround for other Matter projects.
- Google needs a Google hub or Nest device as the Matter controller; Thread devices need a Google Thread border router.
- Because the console project defines the device type, a multi-endpoint device may only show some endpoints.
- This is a known pain point (see [discussion #81](https://github.com/DavidvtWout/esphome-matter/discussions/81) in the upstream project). Reports of what works are very welcome.

## Amazon Alexa

An Echo Gen 4 added an esphome-matter Wi-Fi light (on/off) without any special steps (upstream [issue #71](https://github.com/DavidvtWout/esphome-matter/issues/71), a self-reported result).

1. In the Alexa app, choose **Devices -> + -> Add Device -> Matter**.
2. Scan the QR code or enter the manual code.
3. Alexa warns that uncertified Matter devices may stop working with future updates. Continue.

Notes:

- Alexa expects Matter on port 5540 (the Matter default), which the device uses.
- Alexa does not support every Matter device type. Matter-over-Thread was not tested.

## Samsung SmartThings

No reports of commissioning an esphome-matter device with SmartThings were found. SmartThings supports on/off, dimmable and color lights and plugs from other vendors, so those device types are the most likely to work. Treat everything else as untested. Please report your results.

## IKEA Home smart

The IKEA Home smart app (DIRIGERA hub) accepts esphome-matter devices: open the QR URL from the log and scan it in the app. IKEA only supports a very limited set of Matter device types, and it does not handle multiple endpoints well. In the example config with a button, a temperature sensor and a light, only the temperature sensor showed up. With only a light endpoint configured, it is detected as a light. For the full device, share it with a Home Assistant fabric as well (see multi-admin below).

---

## Multi-admin: sharing a device with more than one ecosystem

A Matter device can belong to several ecosystems (fabrics) at once. The device supports up to **5 fabrics** by default. You can raise this with the `CONFIG_MAX_FABRICS` sdkconfig option (5 to 255):

```yaml
esp32:
  framework:
    type: esp-idf
    sdkconfig_options:
      CONFIG_MAX_FABRICS: "8"
```

Once a device has joined its first fabric, its commissioning window is **closed** on restarts. To add another ecosystem:

1. In the ecosystem that already owns the device, open the device and choose **Share** / **Link another app** / **Turn on pairing mode** (the wording differs per app). This opens a commissioning window and shows a temporary code or QR.
2. Use that temporary code in the second ecosystem. The original code from the device log is not used for this.

Fabric data is stored in the dedicated NVS partition and survives OTA updates. It is independent of the transport, so you can commission over Wi-Fi and later swap in `openthread` without re-commissioning (if the hardware supports both).

If you lose access to every controller, use the `matter.factory_reset` action (see the README's example config) to wipe all fabrics and re-open the commissioning window.

Each ecosystem can have its own friendly name for the device (Matter NodeLabel is per fabric).

## Known quirks

- **Test credentials show up.** Ecosystems show "TEST_VERSION" or a test vendor name unless you set `vendor_name`, `product_name` and `hardware_version_string` in `matter:`.
- **Subset of device types.** Every ecosystem supports only some Matter device types. A device can commission successfully and still expose nothing useful.
- **Multiple endpoints.** IKEA shows only some endpoints. Other ecosystems may merge or hide endpoints. If an ecosystem misbehaves, try a single endpoint first.
- **Restart reconnect.** After a restart the device can take up to about a minute to become reachable again. See [troubleshooting.md](troubleshooting.md#slow-reconnect-after-restart).
- **Matter and the ESPHome API.** Both can run on the same device (they use different ports), but they are independent connections. Heap use with both enabled has not been measured.
- **Future strictness.** Ecosystems may reject test-certified devices in future updates. Production use requires a CSA vendor ID and a proper DAC chain (see the README's Credentials notes).

## What to report

Please report what you tried in the upstream [ecosystem compatibility discussion](https://github.com/DavidvtWout/esphome-matter/discussions/44) or open an issue on this repository. The most useful report contains:

- Ecosystem and app/hub versions (for example "Apple Home iOS 26.x with HomePod mini", "matterjs-server 9.x").
- Device: ESP32 variant, Wi-Fi or Thread, ESPHome version, esphome-matter version.
- Matter device types and endpoints configured.
- Whether commissioning succeeded, and which steps were needed.
- Which controls and sensors worked, and which did not (including state sync in both directions).
- Relevant device log lines (remove the setup code first), and any controller-side error message.
