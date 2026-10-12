"""ESPHome -> Matter attribute writes stay on the right side of the data model.

esp-matter 1.6 has two attribute worlds: clusters that are "code driven" own
their values and must be written through the cluster object, and store clusters
that can be written with esp_matter::attribute::report(). report() skips the
attribute callbacks only for non-writable attributes, so every write that
mirrors ESPHome state is wrapped in a MatterPushGuard. These checks parse the
C++ sources, no compiler needed.
"""

import re

from conftest import COMPONENTS, REPO_ROOT

from components.matter.data_model.attributes import SENSOR_ATTRIBUTES

MATTER_DIR = COMPONENTS / "matter"

# Clusters that esp-matter 1.6 serves from the attribute store (classic ember
# clusters). Everything else is code driven or has its own setter API.
# `wc` is the namespace alias used in matter_cover.cpp.
STORE_CLUSTERS = {"OnOff", "LevelControl", "ColorControl", "WindowCovering", "wc", "Thermostat"}

# Sources that legitimately call attribute::report()/update() without a push
# guard: the user facing set_attribute action (an update that is meant to run
# the callbacks, with its own trigger echo guard) and the sensor store path.
UNGUARDED_ALLOWED = {"matter_attributes.cpp", "matter_sensors.cpp"}

WRITE_RE = re.compile(r"esp_matter::attribute::(report|update)\(\s*([^,]+),\s*([A-Za-z_:]+)::Id\b")
ANY_WRITE_RE = re.compile(r"esp_matter::attribute::(report|update)\(")


def _sources():
    return sorted(MATTER_DIR.glob("*.cpp")) + sorted(MATTER_DIR.glob("*.h"))


def test_mirrored_writes_use_push_guard():
    offenders = []
    for path in _sources():
        text = path.read_text()
        if not ANY_WRITE_RE.search(text) or path.name in UNGUARDED_ALLOWED:
            continue
        if "MatterPushGuard" not in text:
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} write attributes with attribute::report/update without a MatterPushGuard; "
        "writable attributes would echo into the POST_UPDATE callbacks"
    )


def test_store_writes_only_target_store_clusters():
    offenders = []
    for path in _sources():
        if path.name in UNGUARDED_ALLOWED:
            continue
        for match in WRITE_RE.finditer(path.read_text()):
            cluster = match.group(3).split("::")[-1]
            if cluster not in STORE_CLUSTERS:
                offenders.append(f"{path.name}: {cluster}")
    assert not offenders, (
        f"attribute::report/update on {offenders}: these clusters are code driven (or unknown), "
        "write them through registry().Get({endpoint, cluster}) setters"
    )


def test_plain_update_sensor_attributes_are_store_clusters():
    # Plain esp_matter::attribute::update is only right for store clusters. Any
    # other sensor attribute must be flagged code_driven or use a dedicated kind.
    store_sensors = {
        (cluster, attribute)
        for cluster, attributes in SENSOR_ATTRIBUTES.items()
        for attribute, sensor in attributes.items()
        if sensor.kind == "attribute" and not sensor.code_driven
    }
    # PowerSource is served by an attribute access interface from the store.
    assert store_sensors == {("PowerSource", "BatPercentRemaining")}


def test_identify_time_is_read_from_the_cluster():
    text = (MATTER_DIR / "matter_identify.cpp").read_text()
    assert "attribute::get_val" not in text, "Identify is code driven, read IdentifyTime from the cluster"
    assert "GetIdentifyTime" in text


# Code driven Fan Control setters (SetFanMode, ...) are not attribute store writes, so they use a
# scope flag instead of MatterPushGuard.
SCOPE_FLAG_ALLOWED = {"matter_fan.cpp", "matter_fan.h", "matter_climate_fan.cpp", "matter_climate_fan.h"}


def test_no_per_mapping_echo_flags_left():
    for path in _sources():
        if path.name in SCOPE_FLAG_ALLOWED:
            continue
        assert "pushing_" not in path.read_text(), f"{path.name}: use MatterPushGuard"


def test_identify_effect_values_in_docs():
    # Identify.xml EffectIdentifierEnum: Blink 0, Breathe 1, Okay 2,
    # ChannelChange 0x0B, FinishEffect 0xFE, StopEffect 0xFF.
    doc = (REPO_ROOT / "docs" / "device-info.md").read_text()
    assert "11 channel change" in doc
    assert "254 finish effect" in doc
    assert "255 stop effect" in doc


GUARD_DECL_RE = re.compile(r"\bMatterPushGuard\s+\w+\s*(?:[;({=]|$)|\bMatterPushGuard\s*[({]")


def _strip_comments_and_strings(text: str) -> str:
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r'"(?:\\.|[^"\\\n])*"', '""', text)


def _unguarded_write_sites(text: str) -> list[int]:
    """Line numbers of attribute::report/update calls with no MatterPushGuard declared earlier
    in the same or an enclosing `{}` block."""
    stack: list[bool] = [False]  # per open block: a guard was declared in it
    offenders = []
    for number, line in enumerate(_strip_comments_and_strings(text).splitlines(), 1):
        # Walk the line so a guard declared before a call on the same line counts.
        position = 0
        for match in re.finditer(r"[{}]|MatterPushGuard\b[^;{}]*|esp_matter::attribute::(?:report|update)\(", line):
            token = match.group(0)
            if token == "{":
                stack.append(False)
            elif token == "}":
                if len(stack) > 1:
                    stack.pop()
            elif token.startswith("MatterPushGuard"):
                if GUARD_DECL_RE.match(token):
                    stack[-1] = True
            elif not any(stack):
                offenders.append(number)
            position = match.end()
    return offenders


def test_every_mirrored_write_site_is_preceded_by_a_push_guard():
    """Per call site: an actual `MatterPushGuard name;` declaration must precede each
    attribute::report/update call in the same or an enclosing block (comments don't count)."""
    offenders = []
    for path in _sources():
        if path.name in UNGUARDED_ALLOWED:
            continue
        offenders += [f"{path.name}:{n}" for n in _unguarded_write_sites(path.read_text())]
    assert not offenders, f"attribute::report/update without a MatterPushGuard in scope: {offenders}"


def test_guard_check_rejects_comments_and_other_blocks():
    call = "esp_matter::attribute::report(a, b, c, &v);"
    assert _unguarded_write_sites(f"void f() {{\n// MatterPushGuard g;\n{call}\n}}") == [3]
    assert _unguarded_write_sites(f"void f() {{\n{{ MatterPushGuard g; }}\n{call}\n}}") == [3]
    assert _unguarded_write_sites(f"void f() {{\nMatterPushGuard g;\nif (x) {{\n{call}\n}}\n}}") == []
