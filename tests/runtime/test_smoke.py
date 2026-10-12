"""PR smoke test: the real firmware boots in QEMU, can be commissioned, and a light can be toggled and read back.

No subscription is used (priming takes ~90 s under emulation), so this runs in a few minutes once the image is built.
"""

from __future__ import annotations

import pytest

MANUAL_CODE = "34970112332"  # passcode 20202021 / discriminator 3840 from the YAML

pytestmark = pytest.mark.smoke


def test_boot(device):
    log = device.log_text()
    assert "Server ready!" in log
    assert f"Manual pairing code: {MANUAL_CODE}" in log
    # The passcode is fixed in the YAML: the log must not claim one was generated.
    assert "Generated a new commissioning passcode" not in log
    print(f"booted in {device.boot_seconds:.0f} s")


def test_commission(device, matter):
    node_id = matter.commission()
    assert node_id >= 1
    print(f"commissioned as node {node_id} in {matter.commission_seconds:.0f} s")
    assert device.wait_log(r"Commissioning complete", timeout=10), (
        "device did not log 'Commissioning complete'"
    )


def test_light_toggle_and_read(device, matter):
    matter.commission()
    matter.start()
    matter.invoke(1, "OnOff", "On")
    assert matter.read(1, "OnOff", "OnOff") is True
    matter.invoke(1, "OnOff", "Toggle")
    assert matter.read(1, "OnOff", "OnOff") is False
    matter.invoke(1, "OnOff", "Toggle")
    assert matter.read(1, "OnOff", "OnOff") is True
    matter.invoke(1, "OnOff", "Off")
    assert matter.read(1, "OnOff", "OnOff") is False
