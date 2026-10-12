# Documentation index

esphome-matter is an experimental ESPHome external component that exposes ESPHome entities as Matter devices. Start with the [project README](../README.md), then [getting-started.md](getting-started.md). Check [status.md](status.md) to see what is merged and how well it is verified.

## Getting started

- [getting-started.md](getting-started.md): install, flash, commission, add to a controller.
- [status.md](status.md): feature matrix, verification levels, known gaps.
- [examples.md](examples.md): the example config gallery (`examples/`).
- [faq.md](faq.md): frequently asked questions.
- [ROADMAP.md](ROADMAP.md): community demand, maintainer direction, priorities.

## Exposing entities

- [expose.md](expose.md): the `expose:` shortcut (inference, globs, overrides, metering, tags).
- [endpoints.md](endpoints.md): hand-written `endpoints:` and the list of device types.
- [device-types.md](device-types.md): device types and which ESPHome entity each maps to.
- [clusters.md](clusters.md): measurement clusters and extra clusters.
- [actions.md](actions.md): actions and triggers (`matter.send_command`, `set_attribute`, `on_attribute`, ...).
- [binding.md](binding.md): physical buttons controlling other Matter devices.

### Per domain

- [lights.md](lights.md)
- [switch.md](switch.md)
- [sensors.md](sensors.md)
- [binary-sensors.md](binary-sensors.md) (contact, occupancy, leak, smoke/CO)
- [generic-switch.md](generic-switch.md) (buttons and events)
- [fan.md](fan.md)
- [hvac.md](hvac.md) (room air conditioner, air purifier, filter monitoring, heat pump, water heater)
- [climate.md](climate.md) (thermostat and room air conditioner)
- [cover.md](cover.md) (window coverings and garage doors)
- [lock-valve.md](lock-valve.md)
- [power.md](power.md) (power sources, topology, energy)

## Commissioning, network and updates

- [commissioning.md](commissioning.md): setup codes, passcode persistence, fabrics, factory reset.
- [ecosystems.md](ecosystems.md): pairing guide for Apple, Google, Alexa, SmartThings, Home Assistant and IKEA.
- [ble-commissioning.md](ble-commissioning.md): commissioning over Bluetooth LE.
- [ethernet.md](ethernet.md): Matter over Ethernet (experimental).
- [device-info.md](device-info.md): device info, Identify and the fabric limit.
- [matter-ota.md](matter-ota.md): Matter OTA and the `.ota` image tool.
- [troubleshooting.md](troubleshooting.md): IPv6/mDNS, build size, Thread.
- [troubleshooting-network.md](troubleshooting-network.md): slow reconnect after a restart.

## Composition and conformance

- [composition.md](composition.md): endpoint composition, tags and spec conformance rules.
- [conformance.md](conformance.md): the `tools/conformance` checker.

## Developer documentation

- [dev/threading.md](dev/threading.md): threading model.
- [dev/thread-debugging.md](dev/thread-debugging.md): debugging Thread.
- [dev/rfc-entity-extensions/RFC.md](dev/rfc-entity-extensions/RFC.md): draft RFC for per-entity options, with a [demo](dev/rfc-entity-extensions/demo/ok.yaml).
- [design/DIRECTION.md](design/DIRECTION.md): design direction and principles.
- Generated reference: [generated/commands.md](generated/commands.md), [generated/device_types.md](generated/device_types.md), [generated/events.md](generated/events.md).
- Tests and tools: `tests/README.md`, `tools/conformance` ([conformance.md](conformance.md)), `tools/harness` ([README](../tools/harness/README.md)).

## Research

Background research (controller support survey, competing implementations, community feedback, SDK gap analysis) is kept in the maintainer's research notes outside this repository. [ROADMAP.md](ROADMAP.md) summarises it; the controller support summary is in [status.md](status.md).
