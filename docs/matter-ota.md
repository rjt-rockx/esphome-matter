# Matter OTA (over-the-air updates through Matter)

esphome-matter can act as a Matter **OTA Requestor**, so a Matter controller can update the firmware: Home Assistant's Matter server (matterjs-server) with a local OTA provider directory, or Apple Home / Google Home with images published to the Distributed Compliance Ledger (DCL). It is **opt-in** and independent of ESPHome's own `ota:` platform; both can be enabled together.

If you only use ESPHome (`esphome run`, dashboard, `ota: platform: esphome`), you do not need this. Matter OTA mainly matters when the device is a pure Matter device or you want controller-driven updates.

## Configuration

```yaml
matter:
  software_version: 2        # required with OTA; uint32; must increase for every update
  ota: true                  # or an `ota:` / `ota_requestor:` block, see below
```

```yaml
matter:
  software_version: 2
  ota:
    query_interval: 24h      # how often the device asks its provider for an update (default 24h, min 60s)
    watchdog_timeout: 300s   # abort an OTA that is stuck in a non-idle state (default 300s, min 30s)
    on_state:                # string: space_available, download_in_progress, download_complete,
      - logger.log:          #         download_failed, download_aborted, apply_in_progress,
          format: "Matter OTA: %s"   #  apply_complete, apply_failed
          args: ['state.c_str()']
    on_download_started: [ ... ]
    on_download_complete: [ ... ]
    on_download_failed: [ ... ]    # failed or aborted
    on_apply_started: [ ... ]
    on_apply_failed: [ ... ]
```

* `software_version` is the Matter `SoftwareVersion` (uint32, 1..4294967295). It is reported in Basic Information and in the OTA Requestor, and controllers only offer images with a **higher** number than the running one. It is required when OTA is enabled and can also be set on its own.
* The Basic Information `SoftwareVersionString` is set separately with `software_version_string` (default: `esphome: project: version:`, else the ESPHome version, see [device-info.md](device-info.md)). Pass the same string to `--version-string` when building the `.ota` file; it is informational, only the number is compared.
* Matter OTA needs an IP network (`wifi`, `ethernet` or `openthread`). BLE-only commissioning builds are rejected.
* `matter: ota:` (inside the `matter:` block) is unrelated to the top-level ESPHome `ota:` component.

What the component sets up (ESP-IDF sdkconfig, verified against esp-matter 1.6 / connectedhomeip):

| Option | Why |
|---|---|
| `CONFIG_ENABLE_OTA_REQUESTOR=y` | compiles the OTA Requestor; `esp_matter::start()` then calls `esp_matter_ota_requestor_init()` (adds the OTA Requestor cluster to endpoint 0 and the OTA Provider client cluster) and `esp_matter_ota_requestor_start()` |
| `CONFIG_DEVICE_SOFTWARE_VERSION_NUMBER=<software_version>` + `CONFIG_APP_PROJECT_VER_FROM_CONFIG=y` | connectedhomeip reads the numeric version only from this Kconfig (otherwise it is 0) |
| `CONFIG_OTA_AUTO_REBOOT_ON_APPLY=y` | reboot into the new image after Matter applied it |

## Coexistence with ESPHome OTA

Both mechanisms write the other `ota_0` / `ota_1` app slot and switch it with `otadata`, so they never conflict at the flash level. Notes:

* ESPHome splits the flash into two equal app slots (about 1.75 MB each on a 4 MB chip). A Matter firmware is large; check that `firmware.ota.bin` fits (build log shows the size). If it does not, use a custom partition table (`esp32: partitions:`).
* An ESPHome OTA can install a build with a lower `software_version`; the device then reports the lower number and controllers may offer the newer published image again. Keep both paths in sync.
* Rollback: when ESPHome `ota:` and `safe_mode` are both enabled (the default), ESPHome turns on bootloader app rollback and `safe_mode` confirms the new image once it has booted successfully; a Matter-updated image that keeps crashing is rolled back to the previous slot. Without ESPHome `ota:`, rollback is off and the image is confirmed at boot, so a bad Matter update is not rolled back.
* Both updating at once is not coordinated; avoid triggering an ESPHome OTA while a Matter update is downloading.

## Building the `.ota` image

Matter wraps the firmware in an OTA header (vendor id, product id, version, digest). Use the helper, which calls connectedhomeip's `src/app/ota_image_tool.py`:

```
esphome compile device.yaml
tools/make_ota_image.sh \
  --firmware .esphome/build/<name>/.pioenvs/<name>/firmware.ota.bin \
  --version 2 --version-string 1.0.1 \
  --vendor-id 0xFFF1 --product-id 0x8000 \
  --output device-v2.ota
```

(With the native ESP-IDF toolchain the file is under `.esphome/build/<name>/build/`; `firmware.ota.bin` is the app-only image, not the `.factory.bin`.)

* `--version` must equal `software_version` of the build; `--vendor-id` / `--product-id` must equal the device's (`matter: vendor_id` / `product_id`, default test IDs `0xFFF1` / `0x8000`).
* `--min-version` / `--max-version` restrict which running versions may take the update.
* The tool is found via `--ota-tool`, `$OTA_IMAGE_TOOL`, `$ESP_MATTER_PATH`, `$CONNECTEDHOMEIP_PATH`, or is downloaded from GitHub into `~/.cache/esphome-matter`. It needs Python 3 and `curl` for the download.
* Inspect an image with `python3 ota_image_tool.py show device-v2.ota`.

## Home Assistant workflow (matterjs-server with a local provider)

Home Assistant only creates `update` entities for devices that offer Matter OTA, and checks the DCL about every 12 hours. For development images that are not in the DCL, use the server's local OTA provider directory (needs test-net DCL, which esphome-matter devices already require, see [ecosystems.md](ecosystems.md)):

1. Start the server with `--ota-provider-dir /path/to/ota` and `--enable-test-net-dcl` (env `OTA_PROVIDER_DIR`, `ENABLE_TEST_NET_DCL=true`). In the Matter Server app, set the corresponding option and map a folder.
2. Flash a first build with OTA enabled (`software_version: 1`) over ESPHome OTA or serial, and commission it.
3. Build the new firmware with `software_version: 2`, create the `.ota` with `make_ota_image.sh` and copy it into the provider directory.
4. In Home Assistant, force a check (`homeassistant.update_entity` on the device's update entity, or "Check for updates") and install. The device downloads the image over BDX, applies it and reboots; the entity then shows version 2.

For a persistent setup (updates shown without manual forcing) publish the image to the DCL; that requires a real vendor ID, so it is not possible with the test vendor ID.

Apple Home / Google Home only update devices whose images are on the DCL. Apple border routers are known to block the mDNS traffic Thread OTA needs ("Target node did not process the update file").

## Triggers

`on_state` and the `on_download_*` / `on_apply_*` triggers run on the ESPHome main loop; use them to, e.g., publish the state to a text sensor, stop outputs before the reboot, or log. The states come from the platform's image processor events, so they appear only while a Matter update is in progress. `apply_complete` is followed by an automatic reboot.

## Troubleshooting / status

* Verified: configuration validation, code generation, and sdkconfig output (`CONFIG_ENABLE_OTA_REQUESTOR=y`, `CONFIG_DEVICE_SOFTWARE_VERSION_NUMBER`, `CONFIG_APP_PROJECT_VER`). **Not hardware tested**: an end-to-end update against matterjs-server is still to be confirmed on a device.
* "No update entity in HA": check that the device's OTA Requestor cluster exists (Descriptor on endpoint 0), vendor/product ID of the `.ota` match, and that the `.ota` version is higher than the running `SoftwareVersion`.
* The RAM/flash cost of the requestor is a few tens of KB of flash plus BDX buffers; leave OTA off if you do not need it.
