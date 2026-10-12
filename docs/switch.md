# Switches and plugs

An ESPHome `switch` entity (for example a relay) can be bound to a Matter On/Off device type with the `switch_id` option. It works exactly like `light_id` does for [lights](lights.md).

Supported device types:

- `on_off_plug_in_unit` (the default choice for a smart plug / relay)
- `on_off_light`
- `mounted_on_off_control` (a wall-mounted control for a load such as a relay behind a wall plate)

`switch_id` cannot be combined with `light_id` on the same device type. For an `on_off_light` use `light_id` if you want to bind a light entity and `switch_id` if you want to bind a plain switch.

```yaml
matter:
  endpoints:
    1:
      on_off_plug_in_unit:
        switch_id: relay

switch:
  - platform: gpio
    id: relay
    name: Relay
    pin: GPIO4
    restore_mode: ALWAYS_OFF
```

See [smart-plug.yaml](../examples/smart-plug.yaml) for a complete example with a status LED and a factory reset button.

### Pre-defined behaviour

State is synchronized in both directions:

- Matter OnOff attribute changes (On, Off and Toggle commands, attribute writes and the StartUpOnOff behaviour) turn the ESPHome switch on or off.
- Changes of the ESPHome switch state (from Home Assistant, a physical button, an automation, ...) are reported to Matter, so controllers see the new OnOff state.

The ESPHome switch state is the logical state, so an inverted switch (`inverted: true`) is exposed to Matter as the logical state and not as the pin level.

Loops are prevented: a Matter change that matches the current switch state is ignored, and ESPHome state changes are reported to Matter without triggering the Matter-to-ESPHome path again.

### Power-on behaviour

Matter manages the power-on state of the endpoint. After boot, the stored value of the OnOff attribute is applied to the switch, which overrides the ESPHome restore logic. Use `restore_mode: ALWAYS_OFF` on the switch (the ESPHome default for most switch platforms). A warning is logged during config validation for any other restore mode.

The `StartUpOnOff` attribute of the OnOff cluster determines the power-on behaviour on Matter's side. If it is not set the previous state is restored.

### Custom behaviour

If you do not set `switch_id` the endpoint is created without any binding. You can then use the `on_attribute` triggers and `matter.set_attribute` action to implement custom behaviour. See [actions](actions.md).
