"""`persist_passcode: until_commissioned | true | false` (schema and codegen)."""

from __future__ import annotations

import textwrap

from conftest import HEADER, WIFI, COMPONENTS, run_esphome, toolchain_line
import pytest

ENDPOINTS = """
  endpoints:
    1:
      on_off_light: {}
"""


def matter_block(persist: str | None) -> str:
    line = f"  persist_passcode: {persist}\n" if persist is not None else ""
    return "matter:\n" + line + ENDPOINTS.lstrip("\n")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "until_commissioned"),  # new default
        ("until_commissioned", "until_commissioned"),
        ("UNTIL_COMMISSIONED", "until_commissioned"),
        ("true", "true"),
        ("false", "false"),
    ],
)
def test_persist_passcode_modes(run_config, value, expected):
    result = run_config(matter_block(value))
    assert result.ok, result.output
    assert f"persist_passcode: {expected}" in result.output.replace("'", "")


@pytest.mark.parametrize("value", ["sometimes", "0.5", "[1]"])
def test_persist_passcode_invalid(run_config, value):
    result = run_config(matter_block(value))
    assert not result.ok
    assert "persist_passcode" in result.output


def test_persist_passcode_with_yaml_passcode(run_config):
    body = "matter:\n  passcode: 20202021\n  persist_passcode: until_commissioned\n" + ENDPOINTS.lstrip("\n")
    result = run_config(body)
    assert result.ok, result.output


@pytest.mark.slow
@pytest.mark.parametrize(
    ("value", "define"),
    [(None, 2), ("until_commissioned", 2), ("true", 1), ("false", 0)],
)
def test_persist_passcode_define(tmp_path, value, define):
    text = HEADER.format(toolchain=toolchain_line(), components=COMPONENTS) + WIFI
    text += matter_block(value)
    path = tmp_path / "persist.yaml"
    path.write_text(textwrap.dedent(text))
    result = run_esphome("compile", "--only-generate", str(path), cwd=tmp_path)
    assert result.ok, result.output[-3000:]
    defines = (tmp_path / ".esphome/build/matter-unit/src/esphome/core/defines.h").read_text()
    assert f"#define MATTER_PERSIST_PASSCODE_MODE {define}\n" in defines
