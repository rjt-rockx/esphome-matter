# Battery powered Thread devices (Matter ICD)

> **Experimental and not verified on hardware.** Nothing on this page has run on a chip. The configuration validates, the sdkconfig and generated code are snapshot-tested and CI compiles an ESP32-H2 and an ESP32-C6 build, but the sleep behaviour, the power numbers and the controller behaviour are untested. Treat every claim below as "should work, please report".

`matter: icd:` turns a Matter-over-Thread device into an **Intermittently Connected Device** (ICD, Matter 1.4 and later): a Thread *sleepy end device* whose radio polls its parent for messages, while the CPU sleeps in between. It can transmit at any moment (a sensor change goes out immediately); only *receiving* is gated by the poll.

Scope of this first version: the `openthread:` path only (ESP32-C6 and ESP32-H2), Short Idle Time (SIT) and Long Idle Time (LIT) devices, automatic light sleep, and `on_active` / `on_idle` triggers. Not implemented: waking from light sleep on a GPIO edge, ICD over BLE commissioning (no `openthread:` block), Thread CSL / synchronized sleepy end devices, report batching, a TX power option.

It is **not** deep sleep. `deep_sleep:` reboots the chip on every wake: Thread re-attach, CASE session and subscription all start from scratch (seconds at tens of mA), and the controller shows the device as offline in between. The matter component warns when `deep_sleep:` is combined with `matter: icd:`.

## Configuration

```yaml
esphome:
  suspend_loop: true          # recommended; a warning is logged otherwise
esp32:
  variant: ESP32H2
  watchdog_timeout: 60s       # lets the main loop sleep up to 24 s (default 5s caps it at 2 s)
logger:
  baud_rate: 0                # a serial logger keeps the chip awake
openthread:
  device_type: MTD            # required
  poll_period: 15s            # required, above 0
  tlv: !secret thread_tlv
matter:
  icd:
    mode: sit                 # sit (default) | lit
    # Everything below is optional; the defaults depend on `mode` (table below).
    slow_poll_interval: 5s
    fast_poll_interval: 500ms
    idle_interval: 60s
    active_interval: 1s
    active_threshold: 1s
    report_on_active: false
    light_sleep: true
    # LIT only
    clients_per_fabric: 2
    uat_hint: [custom_instruction]
    uat_instruction: Press the button
    on_active:
      - component.update: my_sensor
    on_idle:
      - logger.log: idle
```

| Option | Default (SIT / LIT) | Meaning |
| --- | --- | --- |
| `mode` | `sit` | `sit`: Short Idle Time. `lit`: Long Idle Time with Check-In and User Active Mode Trigger (see below). |
| `slow_poll_interval` | 5 s / 20 s | Poll period while idle. SIT: at most 15 s (Matter spec). LIT: may be longer. |
| `fast_poll_interval` | 500 ms | Poll period in active mode. Must be smaller than `active_interval`. |
| `idle_interval` | 60 s / 600 s | IdleModeDuration, 1 to 64800 s, at least `slow_poll_interval`. **This is also the subscription max interval controllers see**, so it is the liveness heartbeat: the device wakes into active mode and reports at least this often. |
| `active_interval` | 1 s | ActiveModeDuration, at least 300 ms, at most `idle_interval`. |
| `active_threshold` | 1 s / 5 s | ActiveModeThreshold: how long the device stays active after network activity. At least 300 ms (LIT: 5 s), at most 65535 ms. |
| `report_on_active` | `false` | Ask CHIP to emit a report whenever the device enters active mode (`CONFIG_ICD_REPORT_ON_ACTIVE_MODE`). |
| `light_sleep` | `true` | Enable the power management sdkconfig options and call `esp_pm_configure()` with light sleep before Matter starts. |
| `clients_per_fabric` | 2 (LIT) | Check-In clients that can register per fabric. LIT only. |
| `uat_hint` | required (LIT) | How the user can wake the device (`UserActiveModeTriggerHint`). LIT only. Values: `power_cycle`, `settings_menu`, `custom_instruction`, `device_manual`, `actuate_sensor`, `actuate_sensor_seconds`, `actuate_sensor_times`, `actuate_sensor_lights_blink`, `reset_button`, `reset_button_lights_blink`, `reset_button_seconds`, `reset_button_times`, `setup_button`, `setup_button_seconds`, `setup_button_lights_blink`, `setup_button_times`, `app_defined_button`. |
| `uat_instruction` | | `UserActiveModeTriggerInstruction`, at most 128 characters. Required for `custom_instruction` (free text) and for the `*_seconds` / `*_times` hints (a decimal number). LIT only. |
| `on_active`, `on_idle` | | Automations that run on the main loop when the ICD enters active / idle mode. |

The defaults are the values of Espressif's `icd_app` example, not the CHIP Kconfig defaults (those are development values: 2 s idle interval, 200 ms fast poll). Everything is passed to CHIP through its Kconfig options, so CHIP's compile-time `static_assert`s check the combination as well.

### Action

`matter.icd.trigger_active` keeps the device in active mode for `active_threshold`. It is the User Active Mode Trigger: call it when the user actuates the device (a button press) so that a controller command sent right after is answered at fast-poll latency. It needs `matter: icd:`. In a LIT build it is also what `uat_hint` / `uat_instruction` tell the user to do.

```yaml
binary_sensor:
  - platform: gpio
    pin: {number: GPIO4, mode: INPUT_PULLUP, inverted: true}
    on_press:
      - matter.icd.trigger_active
```

## SIT or LIT

| | SIT (default) | LIT |
| --- | --- | --- |
| Slow poll | 15 s at most | longer allowed |
| Extra Matter features | none | Check-In protocol, User Active Mode Trigger, Long Idle Time Support |
| Registration | none | a controller registers as a client; until one does, the device runs as SIT (Dynamic SIT/LIT, on by default) |
| Controllers | works like a normal sleepy Thread device | see below |

**Start with SIT.** A SIT device with a slow poll of 15 s or less looks like an ordinary sleepy Thread device to every controller. A LIT build keeps Dynamic SIT/LIT enabled, so it also runs as SIT until a controller registers, which makes the firmware safe on controllers that do not know LIT; the extra savings only appear where a controller registers.

LIT controller support as researched in October 2026 (from news posts and forums, not tested here, and changing quickly):

* Home Assistant (OHF Matter Server 1.2 beta): supports it as an opt-in ("Battery Saver Mode"). The behaviour of the matter.js stack was still changing, so pin a tested version.
* SmartThings (hub firmware 60.x beta): supports it, on by default, can be disabled per device.
* Apple Home and Google Home: no public LIT support was found. Use SIT.
* Alexa: unknown.

If the device is shared between several controllers (multi-admin), every one of them must cope with its sleep behaviour or the device can look offline in some of them.

## What keeps the chip awake

The goal is that the CPU is in light sleep almost all the time. These stop it, and the validator warns about most of them:

* **The ESPHome main loop.** By default it wakes every 16 ms. `esphome: suspend_loop: true` removes the periodic loop wakeups, but the loop still wakes at least every `2 * watchdog_timeout / 5` (2 s with the default `esp32: watchdog_timeout: 5s`, 24 s at 60 s) and for every scheduler item such as a sensor `update_interval`.
* **`api:`.** The native API connection has keepalive traffic the sleepy device has to poll for. Leave it out of battery builds.
* **`ota: platform: esphome`.** Does not work while `openthread: poll_period` is set. Use [Matter OTA](matter-ota.md). A running Matter OTA transfer keeps the device active (unverified).
* **The logger.** The serial port, in particular USB-Serial-JTAG on the C6 and H2, keeps the chip awake. Use `logger: baud_rate: 0`.
* **GPIO interrupts.** Edge interrupts of a `gpio` binary sensor do **not** wake the chip from light sleep. A door contact is only noticed when something else wakes the CPU (the next poll). A wake-from-GPIO option is future work.
* **Peripherals.** The "power down peripherals in light sleep" and "power down flash" options are deliberately not enabled: they can break ESPHome I2C, SPI and ADC drivers.
* **Polling sensors.** Every report costs roughly 1 mC (estimate), and every scheduler wakeup costs a little too. See the next section.

### Sensor update pattern

Do not let a sensor poll on its own timer. Disable its timer and read it when the ICD enters active mode, which happens at least every `idle_interval`:

```yaml
matter:
  icd:
    idle_interval: 60s
    on_active:
      - component.update: temp_sensor
sensor:
  - platform: sht3xd
    id: temp_sensor
    update_interval: never
    temperature:
      id: temperature
      filters:
        - delta: 0.2
```

The value is then read while the radio is already in fast-poll mode, and goes out in or next to the keepalive report the controller expects. A slow sensor (a 100 ms conversion) may miss that report and send its own, still inside the same active window. `on_active` also fires once shortly after boot, because the ICD starts in active mode before anything can observe it. See [examples/battery-thread-sensor.yaml](../examples/battery-thread-sensor.yaml) for a complete device, with a battery on the root node (see [power.md](power.md)).

## How it works

* The device is a Thread minimal Thread device (`openthread: device_type: MTD`). The sleepy link mode (`rxOnWhenIdle=false`) comes from ESPHome's `poll_period > 0`: esp-matter does not manage OpenThread here. That is why both are required.
* CHIP's **ICD manager** then owns the poll period (slow in idle mode, fast in active mode) and overrides the value from the YAML. `openthread.set_poll_period` actions are rejected because they would fight it.
* The ICD server only exists for a minimal Thread device: with the default `device_type: FTD` it would be silently left out, so the validator requires MTD.
* `light_sleep: true` selects `CONFIG_PM_ENABLE`, `CONFIG_FREERTOS_USE_TICKLESS_IDLE`, `CONFIG_FREERTOS_HZ=1000`, `CONFIG_IEEE802154_SLEEP_ENABLE` and `CONFIG_ESP_PHY_MAC_BB_PD`, and `MatterComponent::setup()` calls `esp_pm_configure()` (CPU frequency fixed, light sleep on) before Matter starts. Nothing else in an ESPHome firmware does.
* `on_active` / `on_idle` come from one `ICDStateObserver` registered with the ICD manager. CHIP has exactly one free observer slot, and this uses it. The callbacks arrive on the CHIP thread and are forwarded to the main loop.
* The User Active Mode Trigger hint and instruction are written into the ICD Management cluster before Matter starts (in the esp-matter data model they exist only on LIT devices, so a SIT device has neither). A SIT device also has no Check-In protocol.
* Subscription reports are not changed: with the ICD server enabled CHIP picks the subscription max interval equal to `idle_interval`.

## Validation

Errors:

* `openthread:` missing, `device_type` not `MTD`, or `poll_period` missing or 0. `wifi:` or `ethernet:` present (ICD is Thread only).
* `openthread.set_poll_period` used anywhere. `matter.icd.trigger_active` used without `matter: icd:`.
* SIT with `slow_poll_interval` above 15 s. `fast_poll_interval` not below `active_interval`. `idle_interval` shorter than `slow_poll_interval` or outside 1 to 64800 s. `active_interval` or `active_threshold` below 300 ms. LIT with `active_threshold` below 5 s.
* `clients_per_fabric`, `uat_hint` or `uat_instruction` with `mode: sit`; LIT without `uat_hint`, or a hint that needs an instruction without one.

Warnings (the build continues):

* `api:` present. `ota: platform: esphome` present. `deep_sleep:` present.
* With `light_sleep: true`: `esphome: suspend_loop` not set and `loop_interval` below 1 s; `esp32: watchdog_timeout` of 5 s or less; a logger with a non-zero `baud_rate`.
* A sensor mapped into Matter whose `update_interval` is shorter than `idle_interval`.
* A variant other than ESP32-C6 and ESP32-H2.

## Hardware-unverified

Everything. The plan for checking it on hardware (an H2 and a C6 board, a Thread border router, a Joulescope or PPK2):

1. Commission, read the ICD Management cluster (`0x0046`: IdleModeDuration, ActiveModeDuration, ActiveModeThreshold, FeatureMap, UAT hint and instruction on LIT).
2. Check in the logs that the poll period switches between slow and fast, the device is a Child with `rxOnWhenIdle=false`, and this survives a parent reboot. Look for deadlocks on idle/active transitions (ESPHome's OpenThread lock and CHIP's stack lock are the same underlying lock).
3. Measure the average current: against Espressif's `icd_app` first, then this firmware with `suspend_loop` on and off, watchdog 5 s and 60 s, logger on and off. The Espressif reference measurements at 20 dBm TX are about 104 µA (H2 SIT), 48 µA (H2 LIT), 174 µA (C6 SIT) and 92 µA (C6 LIT) on average, with sleep floors of about 27 µA (H2) and 54 µA (C6), and TX peaks of about 120 mA (H2) and 345 mA (C6): a CR2032 cannot supply that, use AA or AAA cells or a lower TX power.
4. Check the SRP / DNS-SD TXT records (SII, SAI and the ICD key) on the border router, the subscription max interval, and re-attach after a long border router outage (the Thread child timeout is 240 s by default).
5. Controllers: Apple Home and Google Home with SIT, and with a LIT build left unregistered (it must behave as SIT); Home Assistant and SmartThings with LIT registered.
6. `matter.icd.trigger_active` keeps the device active for `active_threshold`; a Matter OTA transfer completes on an ICD; a 72 hour soak.

Battery life numbers are not published here on purpose: the Espressif figures above are for their example firmware, and the ESPHome overhead (main loop wakeups, scheduler, logger) is unmeasured. The estimate behind the advice to use `suspend_loop` is that a default 16 ms loop would cost milliamps on average, far more than the tens of microamps of the ICD itself.
