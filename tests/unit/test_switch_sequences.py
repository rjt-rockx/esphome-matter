"""Cross-checks the Generic Switch event sequences with the Matter certification tests.

Expected sequences are quoted from connectedhomeip src/python_testing/TC_SWTCH.py
(steps 2.4, 2.5, 2.6, release 1.6). The model is tests/unit/switch_sequence_model.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from switch_sequence_model import SwitchModel, run

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "generic-switch.md"
CPP = ROOT / "components" / "matter" / "matter_generic_switch.cpp"

MS = {}
MSR = {"release": True}
MSR_MSL = {"release": True, "long_press": True}
MSR_MSM = {"release": True, "multi_press": True}
MSR_MSL_MSM = {"release": True, "long_press": True, "multi_press": True}
AS_MSM = {"action_switch": True, "multi_press": True}
AS_MSL_MSM = {"action_switch": True, "long_press": True, "multi_press": True}

I, S, O2, O3 = "initial_press", "short_release", "multi_press_ongoing(2)", "multi_press_ongoing(3)"
LP, LR = "long_press", "long_release"
C = "multi_press_complete"

# (id, features, gesture, expected events, source step, documented in docs table)
CASES = [
    # 2.4: MS & !MSL & !AS & !MSR & !MSM: InitialPress, then no further events.
    ("ms-tap", MS, "t", [I], "2.4 step 5", False),
    ("msr-tap", MSR, "t", [I, S], "2.4 step 5", True),
    ("msr-msl-long", MSR_MSL, "L", [I, LP, LR], "2.4 step 5", True),
    ("msr-msl-tap", MSR_MSL, "t", [I, S], "2.4 step 5", False),
    # 2.5 steps 4a/4b, 5a/5b, 6b/6c
    ("msm-1", MSR_MSM, "t.", [I, S, f"{C}(1)"], "2.5 steps 4a, 4b", True),
    ("msm-2", MSR_MSM, "tt.", [I, S, I, O2, S, f"{C}(2)"], "2.5 steps 5a, 5b", True),
    (
        "msm-3",
        MSR_MSM | {"multi_press_max": 3},
        "ttt.",
        [I, S, I, O2, S, I, O3, S, f"{C}(3)"],
        "2.5 steps 6b, 6c",
        True,
    ),
    # 2.5 8a/8b: tap, then long press: no LongPress/LongRelease, count 2
    ("msl-msm-tap-long", MSR_MSL_MSM, "tL.", [I, S, I, O2, S, f"{C}(2)"], "2.5 steps 8a, 8b", True),
    # 2.5 9a/9b: long press, then tap: a new sequence, no MultiPressOngoing, count 1
    ("msl-msm-long-tap", MSR_MSL_MSM, "Lt.", [I, LP, LR, I, S, f"{C}(1)"], "2.5 steps 9a, 9b", True),
    # 2.6 steps 4a/4b, 5a/5b
    ("as-1", AS_MSM, "t.", [I, f"{C}(1)"], "2.6 steps 4a, 4b", True),
    ("as-2", AS_MSM, "tt.", [I, f"{C}(2)"], "2.6 steps 5a, 5b", True),
    # 2.6 steps 6a/6b: MultiPressMax + 1 presses complete with 0
    ("as-overflow", AS_MSM, "ttt.", [I, f"{C}(0)"], "2.6 steps 6a, 6b", True),
    # 2.6 8a/8b: tap, then long press: one InitialPress, no LongPress, count 2
    ("as-msl-tap-long", AS_MSL_MSM, "tL.", [I, f"{C}(2)"], "2.6 steps 8a, 8b", True),
    # 2.6 9a/9b: long press, then tap: InitialPress, LongPress, LongRelease, InitialPress, count 1
    ("as-msl-long-tap", AS_MSL_MSM, "Lt.", [I, LP, LR, I, f"{C}(1)"], "2.6 steps 9a, 9b", True),
    # No test step: MSM without AS exceeding MultiPressMax.
    ("msm-overflow", MSR_MSM, "ttt.", [I, S, I, O2, S, I, S, f"{C}(0)"], "no TC_SWTCH step", False),
    ("latching", {"latching": True}, "t", ["switch_latched", "switch_latched"], "n/a", False),
]


@pytest.mark.parametrize(
    ("name", "features", "gesture", "expected", "source", "documented"), CASES, ids=[c[0] for c in CASES]
)
def test_sequence(name, features, gesture, expected, source, documented):
    assert run(SwitchModel(**features), gesture) == expected, source


@pytest.mark.parametrize(
    ("name", "expected"), [(c[0], c[3]) for c in CASES if c[5]], ids=[c[0] for c in CASES if c[5]]
)
def test_documented(name, expected):
    """Every sequence of the docs table is one that the model produces."""
    row = ", ".join(f"`{e}`" for e in expected)
    assert row in DOC.read_text(), row


def test_cpp_matches_model():
    """Guards the C++ conditions the model mirrors."""
    src = CPP.read_text()
    assert "if (first_press || !this->action_switch_)" in src
    assert "!first_press && this->multi_press_ && !this->multi_press_overflow_" in src
