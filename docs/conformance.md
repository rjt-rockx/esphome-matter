# Matter conformance checker

`tools/conformance` answers, without hardware and in a couple of seconds per
config: **does this YAML produce a spec-conformant Matter data model?** It can
also check a runtime wildcard-read from a live device with the same rules.

```bash
# one or more ESPHome YAMLs (runs `esphome compile --only-generate`, ~2 s each)
python -m tools.conformance tests/configs/esp32-lights.yaml
python -m tools.conformance tests/configs/*.yaml --json result.json

# a runtime dump ({endpoint: {cluster_id: {attr_id: value}}}) from a device/controller
python -m tools.conformance device-dump.json

# an already exported layout (MATTER_LAYOUT_JSON output)
python -m tools.conformance layout.json
```

Options: `--strict` (warnings fail too), `-v` (show info findings), `--no-compat`
(skip Home Assistant / Apple hints), `--no-baseline` (known SDK gaps count as errors),
`--spec-version 1.4|1.4.2|1.5|1.5.1|1.6`, `--spec-dir DIR`, `--keep-layout FILE`,
`--emit-dump FILE`. Exit status is 1 when any error was found.

The Matter data model XML is read from `$MATTER_DATA_MODEL` (the connectedhomeip
`data_model` directory, which contains `1.4/`, `1.5/`, `1.6/`...) and defaults to
the esp-matter checkout in `/home/user/refs`. It is not vendored (CSA licence);
CI fetches it with a sparse clone (see below).

## How it works

```
YAML --esphome compile --only-generate (MATTER_LAYOUT_JSON=..)--> layout.json
layout.json + esp-matter profile (data/espm_profile.json) --> predicted node model
runtime dump ----------------------------------------------> observed node model
node model + Matter data model XML --> rules --> report (text / JSON / exit code)
```

1. **Layout export** (`components/matter/layout_dump.py`, called once at the end of
   `register_endpoints`): when `MATTER_LAYOUT_JSON` is set, the structure the
   component collected (device types per endpoint, clusters it creates itself,
   feature codes, optional attributes, which clusters have entity-trait-derived
   features, compiled-in radios) is written as JSON. Unset = no effect.
2. **esp-matter profile** (`tools/conformance/espm.py`): which attributes, commands and
   events esp-matter *actually creates* is decided in C++ (`esp_matter_cluster.cpp`,
   `esp_matter_feature.cpp`, `esp_matter_endpoint.cpp`). Those files are regular, so
   the profile is extracted from them once and checked in as
   `data/espm_profile.json` (per cluster: unconditional elements, per-feature elements,
   feature implications, cluster revision; per device type: id, revision and the
   cluster/feature/element creation sequence of its `add()`). Regenerate with
   `python -m tools.conformance.espm --esp-matter <checkout>` when bumping esp-matter.
   This is what makes the static mode meaningful at attribute level.
3. **Evaluator** (`conformance.py`): own implementation over the conformance
   elements of the XML (`mandatory/optional/provisional/deprecate/disallow/described/
   otherwise`, feature/attribute/command/event/condition terms, and/or/xor/not,
   revision and value comparisons, choice groups `O.a`, `O.a+`, `O.a2+`). Three-valued
   logic: a condition whose value cannot be known (e.g. `ManagedAclAllowed`, an
   attribute value in static mode) yields "unknown" and never produces a finding.
4. **Rules** (`checks.py`): see below.
5. **Compat hints** (`compat.py`): warnings (`COMPAT-*`) for optional elements that
   Home Assistant / Apple Home read (UniqueID, SerialNumber, NodeLabel, OTA requestor,
   Thermostat Heating/Cooling, position-aware covers). They never fail a run unless
   `--strict`.
6. **Baseline** (`baseline.py`, `data/known_gaps.json`): findings caused by the SDK or
   known repository gaps are reported as `KNOWN` and do not fail the run. Entries are
   matched on code + `where` regex (+ message regex) and carry a reason and a
   source reference. Delete an entry once the cause is fixed.

### Why not matter.js

matter.js (`@matter/model`) has a conformance engine, but using it would add an npm
dependency and a Node step to every CI job, and it models the *spec*, not what
esp-matter creates; the esp-matter profile would still be needed. The Matter XML
conformance grammar is small (about 20 element/term types, all exercised by a unit
test over the whole data model), so a Python evaluator keeps the tool a single
`python -m` call with no extra runtime. The runtime-dump mode makes cross-checking
against matter.js or chip-tool output easy if wanted.

## Rules

| Code | Meaning |
|---|---|
| `NODE-ROOT`, `EP-DEVTYPE-CLASS` | Root Node only on endpoint 0, application device types not on endpoint 0 |
| `EP-DESCRIPTOR`, `EP-DEVTYPES`, `EP-DEVTYPE-DUP/UNKNOWN/REVISION` | Descriptor present, device type list valid; advertised revision newer than the data model is an error, older is info |
| `DT-CLUSTER-MISSING/DISALLOWED` | Mandatory (server/client) clusters of every device type and of the Base Device Type, with conditions such as `Simple`, `Client`, `Duplicate`, `Wi-Fi`, `Thread` evaluated per endpoint |
| `DT-FEATURE-*`, `DT-ATTR-*`, `DT-COMM-*`, `DT-EVEN-*` | Device type overrides of cluster features, attributes, commands and events (`current` = the advertised device type revision) |
| `CL-FEATURE-*` | FeatureMap bits defined, mandatory/disallowed/not-applicable/provisional features, feature dependencies |
| `CL-CHOICE` | Choice groups (`O.a`, `O.a+`, ...) across features, attributes, commands, events |
| `CL-ATTR-*`, `CL-CMD-*`, `CL-EVENT-*` | Mandatory elements present, disallowed or not-applicable absent, undefined elements flagged (generated commands are checked separately from accepted ones) |
| `CL-REVISION`, `CL-GLOBAL-MISSING`, `CL-ATTRLIST-*` | (dump) ClusterRevision, global attributes, AttributeList consistency |
| `EP-SINGLETON`, `EP-ELECTRICAL` | Root-only (singleton) clusters on other endpoints; Electrical Sensor needs a measurement cluster |
| `EP-PARTS`, `EP-SERVERLIST` | (dump) PartsList reachability/cycles, Descriptor ServerList matches the clusters read |

Severity: `error` = non-conformant; `warning` = likely problem (not-applicable
element present, provisional feature, controller hint); `info` = tool limits and
informational notes (e.g. an older advertised device type revision); `known` =
baselined.

## Limits

* Features of Window Covering, Thermostat and Fan Control bound to an ESPHome
  entity come from the entity traits in C++ at runtime. They are marked
  `dynamic_features`; findings that depend on them are downgraded to info. Use a
  runtime dump for the final word.
* Clusters that esp-matter creates in a way the profile parser does not model are
  reported once as `CL-ATTR-UNVERIFIED` and only their features are checked.
* Attribute *values* and constraints (ranges, nullability) are not evaluated; only
  presence, conformance and the value-dependent conformance terms.
* Cross-endpoint rules cover the flat endpoint layout esphome-matter uses
  (everything under the root). Tree/composed layouts are checked only through the
  dump-mode PartsList rules.

## Runtime dumps

Any wildcard read that yields `endpoint -> cluster -> attribute -> value` works;
include the global attributes (`0xFFFC` FeatureMap, `0xFFFB` AttributeList,
`0xFFF9` AcceptedCommandList, `0xFFF8` GeneratedCommandList, `0xFFFA` EventList,
`0xFFFD` ClusterRevision) and the Descriptor cluster (`0x1D`: DeviceTypeList as a list of
`{"deviceType": id, "revision": n}`, ServerList, ClientList, PartsList). Keys may be
decimal or `0x` hex strings. A flat `{"1/6/0": value}` map (python-matter-server
`node.attributes`) is also accepted. `python -m tools.conformance <yaml>
--emit-dump predicted.json` writes the predicted model in the same format, so a real
capture can be diffed against the prediction.

## CI

Add a job next to `unit-tests` (the integrator edits `ci.yml`):

```yaml
  conformance:
    name: Matter conformance
    runs-on: ubuntu-latest
    env:
      MATTER_CONFORMANCE_REQUIRE: "1"
      MATTER_DATA_MODEL: ${{ github.workspace }}/connectedhomeip/data_model
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.DEFAULT_PYTHON }}
      - name: Fetch the Matter data model (public, not vendored)
        run: |
          git clone --depth 1 --filter=blob:none --sparse https://github.com/project-chip/connectedhomeip.git
          git -C connectedhomeip sparse-checkout set data_model/1.6
      - name: Install ESPHome and pytest
        run: |
          python -m venv .venv
          .venv/bin/python -m pip install "esphome @ git+https://github.com/esphome/esphome.git@dev" pytest
      - name: Conformance unit tests
        run: .venv/bin/python -m pytest tests/unit/test_conformance.py
      - name: Check every test config
        run: .venv/bin/python -m tools.conformance tests/configs/*.yaml --json conformance.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: conformance-report
          path: conformance.json
```

The existing `unit-tests` job also picks up `tests/unit/test_conformance.py`; the rule
tests skip when `MATTER_DATA_MODEL` is unset (the evaluator tests always run).
