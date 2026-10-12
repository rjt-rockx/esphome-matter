Almost all device types can be created in esphome-matter, but some need extra configuration. Creating a device type only lets other Matter devices see and address it. It does not mean ESPHome does something useful with the clusters yet. Anything marked "untested" has not been verified on hardware or against a real controller.

See the [feature matrix in the README](../README.md#feature-matrix) for a one-page summary per ESPHome domain.

# Lights

The Matter light device types are `on_off_light`, `dimmable_light`, `color_temperature_light`, and `extended_color_light`. Each adds more functionality to the previous one:

| Device type | Adds | Entity binding |
|---|---|---|
| `on_off_light` | On/Off | `light_id` |
| `dimmable_light` | Level Control (brightness) | `light_id`, `min_level`, `max_level` |
| `color_temperature_light` | Color Control: color temperature (mireds) | `light_id` |
| `extended_color_light` | Color Control: XY color | `light_id` |

Level, color temperature and color are supported, and state is synchronized both ways between the ESPHome light and Matter. Hue/saturation commands from controllers are not implemented; controllers that drive color with XY work. See [lights.md](lights.md) for details, startup behaviour and the FAQ, and [examples/extended-color-light.yaml](../examples/extended-color-light.yaml) for a full example.

```yaml
matter:
  endpoints:
    1:
      on_off_light:
        light_id: light_id
    2:
      dimmable_light:
        light_id: light_id
    3:
      color_temperature_light:
        light_id: light_id
    4:
      extended_color_light:
        light_id: light_id
```

# Plugs and relays

The `on_off_plug_in_unit` and `mounted_on_off_control` device types (and `on_off_light`) can be mapped to an esphome `switch` using `switch_id`. State is synchronized both ways. See [switch.md](switch.md).

```yaml
matter:
  endpoints:
    1:
      on_off_plug_in_unit:
        switch_id: relay
```

# Controller switches (bindings)

The Matter *client* switch device types are `on_off_light_switch`, `dimmer_switch` and `color_dimmer_switch`. They do not expose an ESPHome entity. Instead, ESPHome actions are mapped to the endpoint, and the device sends commands to whatever it is bound to.

```yaml
matter:
  endpoints:
    1:
      on_off_light_switch:
    2:
      id: dimmer_endpoint
      dimmer_switch:
    3:
      color_dimmer_switch:

binary_sensor:
  - platform: gpio
    name: "Up Button"
    pin:
      number: GPIO0
      mode:
        pullup: true
        input: true
      inverted: true
    on_click:
      matter.send_command: dimmer_endpoint.on_off.on
    on_press:
      matter.send_command:
        path: dimmer_endpoint.level_control.move_with_on_off
        arguments:
          move_mode: up
          rate: 20%/s
    on_release:
      matter.send_command: dimmer_endpoint.level_control.stop_with_on_off
```

For a complete overview of supported actions, see [actions.md](actions.md) and [binding.md](binding.md). Binding works but can occasionally take a few seconds before a command is sent.

# Covers

> **Placeholder.** ESPHome `cover:` to Matter Window Covering is documented in [cover.md](cover.md).

See [cover.md](cover.md).

# Fans

> **Placeholder.** ESPHome `fan:` to Matter Fan Control is documented in [fan.md](fan.md).

See [fan.md](fan.md).

# Locks & valves

> **Placeholder.** ESPHome `lock:` and `valve:` to Matter Door Lock and Water Valve are documented in [lock-valve.md](lock-valve.md).

See [lock-valve.md](lock-valve.md).

# Binary sensors

> **Placeholder.** The full binary sensor mapping (including `device_class` guidance) is documented in [binary-sensors.md](binary-sensors.md). The summary below reflects the current code.

`contact_sensor`, `rain_sensor`, `water_leak_detector` and `water_freeze_detector` expose the Boolean State cluster (key `boolean_state`). `occupancy_sensor` instead exposes the Occupancy Sensing cluster (key `occupancy`).

```yaml
matter:
  endpoints:
    1:
      contact_sensor:
        boolean_state: binary_sensor_id
    2:
      rain_sensor:
        boolean_state: binary_sensor_id
    3:
      occupancy_sensor:
        occupancy: binary_sensor_id
```

Mapping is explicit: the `device_class` of the ESPHome entity is not read. The water leak and water freeze detectors can be created, but their binding is untested. Which polarity a controller shows for a contact sensor (open vs closed) is unverified. `soil_sensor` is **not** a Boolean State device; see Sensors below.

See [binary-sensors.md](binary-sensors.md).

# Sensors

> **Placeholder.** The authoritative per-sensor reference (units, scaling, `device_class`, battery, energy) is in [sensors.md](sensors.md). The summary below reflects the current code.

### Simple measurement sensors

`temperature_sensor`, `humidity_sensor`, `light_sensor`, `pressure_sensor` and `flow_sensor` expose a measurement cluster with a `MeasuredValue` attribute. The `MinMeasuredValue`, `MaxMeasuredValue` and optional `Tolerance` attributes aren't supported yet.

```yaml
matter:
  endpoints:
    1:
      temperature_sensor:
        temperature: sensor_id
    2:
      humidity_sensor:
        relative_humidity: sensor_id
    3:
      # The LightSensorType attribute is not settable yet.
      light_sensor:
        illuminance: sensor_id
    4:
      # The pressure_sensor `Extended` feature is not supported yet.
      pressure_sensor:
        pressure: sensor_id
    5:
      flow_sensor:
        flow: sensor_id
```

The pressure value is passed to Matter without scaling, so it is only correct if the ESPHome sensor reports the unit Matter expects. Check [sensors.md](sensors.md) before relying on it.

### soil_sensor

The `soil_sensor` exposes a Temperature Measurement and a Soil Measurement cluster (not Boolean State). The keys are `temperature` and `soil_moisture`. Untested.

```yaml
matter:
  endpoints:
    1:
      soil_sensor:
        temperature: temperature_id
        soil_moisture: soil_moisture_id
```

### Binary sensors

`contact_sensor`, `occupancy_sensor`, `water_leak_detector`, `water_freeze_detector`, `rain_sensor` and `smoke_co_alarm`
bind ESPHome `binary_sensor` entities. The `device_class` of the binary sensor selects the contact polarity and the
occupancy sensor type, see [binary-sensors.md](binary-sensors.md).

```yaml
matter:
  endpoints:
    1:
      contact_sensor: door_sensor_id   # device_class: door is inverted to Matter's "true = closed" automatically
    2:
      occupancy_sensor: motion_sensor_id
    3:
      water_leak_detector: leak_sensor_id
    4:
      rain_sensor: rain_sensor_id
```

### air_quality_sensor

The `air_quality_sensor` supports many concentration measurements as well as temperature and humidity. Each measurement gets its own cluster, and a cluster is only created when a sensor id is mapped to it.

```yaml
matter:
  endpoints:
    1:
      air_quality_sensor:
        temperature: temperature_id
        relative_humidity: humidity_id
        carbon_monoxide: co_id
        carbon_dioxide: co2_id
        nitrogen_dioxide: no2_id
        ozone: ozone_id
        pm_1: pm_1_id
        pm_2_5: pm_2_5_id
        pm_10: pm_10_id
        formaldehyde: formaldehyde_id
        total_voc: total_voc_id
        radon: radon_id
```

### electrical_sensor

This device type exposes clusters and attributes for power and energy measurements (voltage, current, power, frequency, power factor and cumulative energy). See [sensors](sensors.md#electrical-measurements) for the configuration.

# generic_switch

The `generic_switch` is a stateless programmable button that emits Matter events (press, long press, multi press). See [generic-switch.md](./generic-switch.md).

# Untested / unsupported

The device types below can usually be created (add the key under an endpoint), but nothing binds an ESPHome entity to them, so besides sensor attributes and `on_attribute` triggers little interaction is possible. Several of them (`on_off_plug_in_unit`, `window_covering`, `fan`, `door_lock`, `water_valve`, `thermostat`) are being given entity bindings; see the placeholder sections above and [ROADMAP.md](ROADMAP.md). Treat everything in this list as untested.

- door_lock
- aggregator
- power_source
- ota_requestor
- bridged_node
- ota_provider
- root_node
- solar_power
- battery_storage
- secondary_network_interface
- mode_select
- fan
- air_purifier
- water_freeze_detector
- water_valve
- water_leak_detector
- refrigerator
- temperature_controlled_cabinet
- room_air_conditioner
- laundry_washer
- robotic_vacuum_cleaner
- dishwasher
- smoke_co_alarm
- cook_surface
- cooktop
- microwave_oven
- extractor_hood
- oven
- laundry_dryer
- thread_border_router
- dimmable_plug_in_unit
- mounted_dimmable_load_control
- audio_doorbell
- camera
- video_doorbell
- chime
- doorbell
- window_covering
- closure
- closure_panel
- closure_controller
- thermostat
- pump
- pump_controller
- heat_pump
- thermostat_controller
- evse
- device_energy_management
- water_heater
- electrical_utility_meter
- electrical_energy_tariff
- electrical_meter
- control_bridge
