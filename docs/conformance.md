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
(skip Home Assistant / Apple hints), `--baseline FILE` (use another known-gaps file), `--no-baseline` (known SDK gaps count as errors),
`--spec-version 1.4|1.4.2|1.5|1.5.1|1.6`, `--spec-dir DIR`, `--keep-layout FILE`,
`--emit-dump FILE`. Exit status is 1 when any error was found.

The Matter data model XML is read from `$MATTER_DATA_MODEL` (the connectedhomeip
`data_model` directory, which contains `1.4/`, `1.5/`, `1.6/`...) and defaults to
the esp-matter checkout in `/home/user/refs`. It is not vendored (CSA licence);
CI fetches it with a sparse clone (see below).

The checker knows the delegate layer ([delegates.md](delegates.md)): clusters with a configured delegate get the attributes, commands and events that `attach_delegate()` creates (`ChangeToMode`, `SupportedModes`, the configured Operational State commands, ...). Device types such as `evse` are mapped to the esp-matter endpoint namespace (`energy_evse`) through `espmatter_names.json`.

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
   features, compiled-in radios) is written as JSON. Attributes, commands and events
   that the component creates with its own build code (hold time, self test, alarm
   events, the refrigerator mode cluster; `components/matter/sensor_extras.py`) are
   listed per cluster under `extra`. Unset = no effect.
2. **esp-matter profile** (`tools/conformance/espm.py`): which attributes, commands and
   events esp-matter *actually creates* is decided in C++ (`esp_matter_cluster.cpp`,
   `esp_matter_feature.cpp`, `esp_matter_endpoint.cpp`). Those files are regular, so
   the profile is extracted from them once and checked in as
   `data/espm_profile.json` (per cluster: unconditional elements, per-feature elements,
   feature implications, cluster revision; per device type: id, revision and the
   cluster/feature/element creation sequence of its `add()`). Regenerate with
   `python -m tools.conformance.espm --esp-matter <checkout>` when bumping esp-matter.
   A profile feature key can name several features (`cumulative_energy+imported_energy`):
   the element exists only when all of them are enabled, which is how esp-matter creates
   `CumulativeEnergyImported` (not `...Exported`) for an import-only meter.
   This is what makes the static mode meaningful at attribute level.
   Clusters that esp-matter registers as *code-driven* connectedhomeip ServerClusters are
   corrected by `_apply_code_driven_overlay` in `espm.py`, because for them the wire
   AttributeList / AcceptedCommandList come from the cluster class (the provider asks
   `mRegistry.Get(path)` first), not from what `esp_matter_cluster.cpp` puts in the store.
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
| `BR-AGG-*`, `BR-NODE-*`, `BR-UNIQUEID`, `BR-NODELABEL`, `BR-PARTS` | Bridges ([bridge.md](bridge.md)): Aggregator is a direct child of the root and has no application device type, Bridged Nodes are direct children of an Aggregator and have a UniqueID/NodeLabel that is unique and fits 32 bytes, an Aggregator PartsList lists all descendants (dump); UniqueID is only required from BDBI cluster revision 4 (dump) |

Severity: `error` = non-conformant; `warning` = likely problem (not-applicable
element present, provisional feature, controller hint); `info` = tool limits and
informational notes (e.g. an older advertised device type revision); `known` =
baselined.

## SDK gaps that turned out not to be gaps

Both were baselined at first and are now modelled instead (no workaround code is needed
in the component, nothing to fork):

* **General Diagnostics `DeviceLoadStatus`** (mandatory since revision 3). The legacy
  `general_diagnostics::create()` does not create it, but the cluster is code-driven
  (`GeneralDiagnosticsCluster`, registered by `ESPMatterGeneralDiagnosticsClusterServerInitCallback`),
  and its constructor `ForceSet`s `UpTime` and `DeviceLoadStatus`; `Attributes()` lists them and
  `ReadAttribute` encodes a `DeviceLoadStruct` from the InteractionModelEngine. So a read returns a
  valid value and the attribute is in the AttributeList.
* **Network Commissioning Wi-Fi/Thread-only commands on Ethernet.** The store holds
  ScanNetworks/RemoveNetwork/ConnectNetwork/ReorderNetwork (+ responses) unconditionally, but the
  code-driven `NetworkCommissioningCluster::AcceptedCommands/GeneratedCommands` only advertise them
  with the Wi-Fi or Thread feature, so Ethernet-only builds expose none on the wire. (For reference,
  esp-matter does have `esp_matter::command::destroy(cluster, command)` should a store-level removal
  ever be wanted, but it is not needed.) Both are verified by reading the SDK sources, not on
  hardware: confirm with a runtime dump from an Ethernet build (`python -m tools.conformance dump.json`).

## Advertised device type revisions (INFO findings)

`EP-DEVTYPE-REVISION` INFO entries come from esp-matter's *legacy* constants in
`esp_matter_endpoint_impl.h` (Water Leak 1, Water Freeze 1, Rain 1, Pressure 2, Humidity 2, Light
Sensor 3, Door Lock 3, Window Covering 5, Thermostat 4). They are stale hard-coded values from an
older spec, not tied to the cluster contents `add()` creates. The generated data model shipped in the
same package (`data_model/generated/device_types/*`) carries the current values (Water Leak 2, Freeze 2,
Rain 2, Pressure 3, Humidity 3, Light Sensor 4, Door Lock 4, Window Covering 6, Thermostat 6). The legacy
numbers are what the node really implements (the legacy `add()` creates the clusters of that older
revision), so advertising them is honest; bumping them without adding the newer mandatory elements
would claim conformance the node does not have. Recommendation: leave as is (INFO), and revisit
when moving to the generated data model.

## Limits

* Features of Window Covering, Thermostat and Fan Control bound to an ESPHome
  entity come from the entity traits in C++ at runtime. They are marked
  `dynamic_features`; findings that depend on them are downgraded to info. Use a
  runtime dump for the final word.
* Clusters that esp-matter creates in a way the profile parser does not model are
  reported once as `CL-ATTR-UNVERIFIED` and only their features are checked.
* Attribute *values* and constraints (ranges, nullability) are not evaluated; only
  presence, conformance and the value-dependent conformance terms.
* Layout mode knows the configured `parent:` of every endpoint: the Duplicate
  condition compares siblings under the same parent and the bridge rules (`BR-*`)
  follow the parent chain. A dump only has PartsLists; the parent is the holder with
  the shortest list.

## Runtime dumps

Any wildcard read that yields `endpoint -> cluster -> attribute -> value` works;
include the global attributes (`0xFFFC` FeatureMap, `0xFFFB` AttributeList,
`0xFFF9` AcceptedCommandList, `0xFFF8` GeneratedCommandList, `0xFFFA` EventList,
`0xFFFD` ClusterRevision) and the Descriptor cluster (`0x1D`: DeviceTypeList as a list of
`{"deviceType": id, "revision": n}`, ServerList, ClientList, PartsList). Keys may be
decimal or `0x` hex strings. Accepted flavours: the nested form above; a flat
`{"1/6/0": value}` map; a raw python-matter-server / Home Assistant node dump
(`{"node_id": .., "attributes": {"ep/cluster/attr": v}}`, also under `result` or
`node`); and a matter.js harness dump (`tools/harness dump`:
`{"nodeId": .., "data": {"1": {"0x0006": {"0x0000": v}}}}`, where FeatureMap may be an
object of named booleans). `python -m tools.conformance <yaml>
--emit-dump predicted.json` writes the predicted model in the same format, so a real
capture can be diffed against the prediction.

## Diffing against a real device

```
python -m tools.conformance diff <ours> <reference> [--ignore-root] [--ignore-noise] [--json out.json]
```

* `ours`: an ESPHome YAML (layout generated through `esphome`), a `MATTER_LAYOUT_JSON`
  layout, or any dump flavour above.
* `reference`: a dump / layout / YAML file, or a **catalog id**. Ids resolve through
  `*.jsonl` files in `$MATTER_DEVICE_CATALOG` (default `/home/user/research/devices`);
  the entry's `local_dump` is loaded, or, if there is none, the entry's endpoint summary
  (device types, cluster features, parents; attribute lists and tags are then not compared).

Endpoints are matched by device type set (then by best overlap) and order, never by
endpoint number. Per matched pair the diff covers device types and revisions,
server/client clusters, ClusterRevision, FeatureMap bits (decoded to feature names),
AttributeList / AcceptedCommandList / GeneratedCommandList / EventList (spec names),
Descriptor tags, FixedLabel and the parent endpoint (from the layout's configured
parent, or from PartsList in a dump). Lists that one side does not know (clusters the
esp-matter profile cannot predict, a trimmed interview without EventList) are skipped on
both sides, and the sub-items of a cluster present on one side only are folded into the
cluster line. Output is a table (`-` only in the reference, `+` only in ours, `~`
differs), a gap count by class and a 0-100 % match score
(`2 * common facts / (ours + reference facts)`); `--json` writes everything,
including per-endpoint scores. Gaps are what the reference has that ours lacks or has
differently; extras are what only ours has.

* `--ignore-root` skips endpoint 0 (the infrastructure clusters).
* `--ignore-noise` also drops cluster / device type revisions, vendor-specific clusters
  and attributes, and runtime diagnostics clusters on application endpoints.
* `--fail-on-gap` exits 1 when there is any gap. Exit code 2: input or reference not found.

```
python -m tools.conformance gallery examples/devices [--layout-cache DIR] [-j N] [--markdown] [--json out.json]
```

For every YAML below the directory whose first 60 lines contain
`# mirrors: <catalog-id>` (several ids may be comma separated) the layout is generated
and diffed against the reference. The summary matrix lists device (catalog id), variant
(file name without the id prefix), match score, total gaps and gaps per class: `EP`
unmatched endpoints, `DT` device types, `CL` clusters, `FEAT` features, `ATTR`, `CMD`,
`EVT`, `COMP` composition (tags, labels, parent); `extra` counts what only ours has.
`--layout-cache DIR` keeps `<stem>.layout.json` so reruns skip code generation
(`--refresh` regenerates). A YAML that fails to generate or has an unknown id becomes an
`ERROR` row and the exit code is 1.

The layout export (`MATTER_LAYOUT_JSON`) carries `parent`, `label` and `tags` per endpoint
for this; older layouts lack them and are compared without composition.

## CI

The `conformance` job in `.github/workflows/ci.yml` (blocking) is, abridged (this is a GitHub Actions workflow, not an ESPHome config; action versions in the file are newer):

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
        run: .venv/bin/python -m pytest tests/unit/test_conformance_checker.py
      - name: Check every test config
        run: .venv/bin/python -m tools.conformance tests/configs/*.yaml --json conformance.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: conformance-report
          path: conformance.json
```

The existing `unit-tests` job also picks up `tests/unit/test_conformance_checker.py`; the rule
tests skip when `MATTER_DATA_MODEL` is unset (the evaluator tests always run).
