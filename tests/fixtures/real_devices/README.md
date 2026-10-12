# Real device node dumps

Node dumps (python-matter-server format: `{"node_id", "attributes": {"endpoint/cluster/attribute": value}}`)
used by `tests/unit/test_conformance_diff.py` to exercise `python -m tools.conformance diff`.

They are copied unmodified from the Home Assistant core repository,
`tests/components/matter/fixtures/nodes/` (https://github.com/home-assistant/core),
which is licensed under the Apache License 2.0 (https://github.com/home-assistant/core/blob/dev/LICENSE.md).
Copyright the Home Assistant Authors.

| file | device | why |
|---|---|---|
| `mock_onoff_light.json` | CHIP test-app on/off light | simplest application endpoint |
| `mock_fan.json` | CHIP test-app fan | FixedLabel on endpoints, FeatureMap bits |
| `mock_window_covering_full.json` | CHIP test-app window covering | feature-rich cluster |
| `eve_contact_sensor.json` | Eve Door & Window (Thread) | real vendor firmware, vendor clusters |
| `ikea_bilresa_dual_button.json` | IKEA BILRESA dual button | two sibling endpoints with semantic tags |
