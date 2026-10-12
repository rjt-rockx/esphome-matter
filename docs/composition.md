# Endpoint composition and spec conformance

The `endpoints:` engine takes care of several Matter rules about how endpoints relate to each other. Most of them need no configuration.

## Sibling endpoints with the same device type (TagList)

Sibling endpoints that share an application device type, such as four relays or two temperature sensors, are a "Duplicate" in the Matter device library. Such endpoints must carry a Descriptor `TagList` so that a controller can tell them apart.

esphome-matter adds one automatically: a *Common Number* semantic tag (namespace `0x07`, `One`, `Two`, ...) in order of endpoint ID. Only endpoints that have the same parent and an overlapping device type count as siblings. Utility device types (Power Source, Electrical Sensor, Bridged Node, ...) are ignored. The build log lists the endpoints that received a tag.

To choose the tags yourself use `tags:`. An endpoint with `tags:` is left alone. Up to three tags per endpoint are possible (an esp-matter limit):

```yaml
matter:
  endpoints:
    10:
      tags:
        - namespace: common_position   # or a number, e.g. 8
          tag: left                    # or a number
          label: Left                  # optional, up to 16 bytes
      generic_switch:
    11:
      tags:
        - namespace: common_position
          tag: right
      generic_switch:
```

Namespaces by name: `common_number`, `common_position`, `switches`. Any other namespace can be given as a number; the tag is then a number as well. Siblings with a shared device type must not have identical tag sets; this is checked at build time.

## Labels

`label:` publishes a FixedLabel cluster with the single label `name: <label>` on the endpoint. Home Assistant uses it to name entities of multi-endpoint devices. The value is limited to 16 bytes by the Matter specification. If the endpoint also receives an automatic Number tag, the label is added to that tag as its `Label`.

Using `label:` enables `CONFIG_CUSTOM_DEVICE_INFO_PROVIDER`: esphome-matter installs a small `DeviceInfoProvider` that serves the FixedLabel lists. User labels are not supported and read as empty.

## Parent endpoints

Every endpoint has an explicit parent. The default is endpoint 0, which makes it appear in the root `PartsList`. Use `parent:` to compose an endpoint below another one; the parent's `PartsList` then lists its children and the root `PartsList` lists all of them.

```yaml
matter:
  endpoints:
    30:
      on_off_plug_in_unit:
        switch_id: strip_relay
    31:
      parent: 30
      temperature_sensor: strip_temp
```

`parent:` must name a configured endpoint and cannot form a cycle. From C++ the same link is `register_endpoint_parent(endpoint, parent)` on the `MatterComponent`.

## ConfigurationVersion

Controllers cache an endpoint's structure. Basic Information `ConfigurationVersion` tells them when to read it again. The code generator hashes the structure of the node (endpoint IDs, parents, device types, server clusters, enabled features, tags and labels). At boot the hash is compared to the one stored in NVS (namespace `esphome-matter`); when it differs the version is incremented and persisted. Adding, removing or changing an endpoint therefore bumps the version at the first boot of the new firmware.

A device without a stored hash, which includes a first boot, reports version 2, because earlier firmware always reported 1.

The ESP32 platform serves a compile time constant and cannot store the version, so esphome-matter redirects it to its own value through `matter_chip_config.h`. The version is read by controllers when they (re)connect; it is not announced to a controller that stays subscribed across the reboot.

## Limits

Endpoint 0 is the root node, which exists already. Configure it only to add a node level `battery:` / `wired:` Power Source or an `electrical_sensor` to it, see [power.md](power.md). The build sets `CONFIG_ESP_MATTER_MAX_DYNAMIC_ENDPOINT_COUNT` to the number of configured endpoints (without endpoint 0) plus the root node and `CONFIG_ESP_MATTER_MAX_DEVICE_TYPE_COUNT` to the largest number of device types on one endpoint. Both are exact; the esp-matter defaults are 16.

An [example](../examples/multi-relay.yaml) with four relays is available.
