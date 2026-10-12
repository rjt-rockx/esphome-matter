"""Pure-Python reference of the event generation of MatterGenericSwitchMapping.

This mirrors ``MatterGenericSwitchMapping::on_press_/on_release_/finish_multi_press_``
in components/matter/matter_generic_switch.cpp, so the documented sequences can be
cross-checked against the TC_SWTCH 2.4/2.5/2.6 expectations without hardware. Keep
both in sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SwitchModel:
    latching: bool = False
    release: bool = False  # MomentarySwitchRelease (MSR)
    long_press: bool = False  # MomentarySwitchLongPress (MSL)
    multi_press: bool = False  # MomentarySwitchMultiPress (MSM)
    action_switch: bool = False  # ActionSwitch (AS)
    multi_press_max: int = 2
    events: list[str] = field(default_factory=list)
    _pressed: bool = False
    _long_sent: bool = False
    _count: int = 0
    _overflow: bool = False

    def _emit(self, name: str, count: int | None = None) -> None:
        self.events.append(name if count is None else f"{name}({count})")

    def press(self) -> None:
        if self.latching:
            self._emit("switch_latched")
            return
        if self._pressed:
            return
        self._pressed = True
        self._long_sent = False
        if not self.multi_press:
            self._count = 0
        self._count = min(self._count + 1, 255)
        if self._count > self.multi_press_max:
            self._overflow = True
        first = self._count == 1
        # Momentary: InitialPress on every press. Action switch: first press only.
        if first or not self.action_switch:
            self._emit("initial_press")
            if not first and self.multi_press and not self._overflow:
                self._emit("multi_press_ongoing", self._count)

    def hold(self) -> None:
        """The button stays pressed for long_press_time."""
        if self._pressed and self.long_press and self._count == 1:
            self._long_sent = True
            self._count = 0
            self._overflow = False
            self._emit("long_press")

    def release_button(self) -> None:
        if self.latching:
            self._emit("switch_latched")
            return
        if not self._pressed:
            return
        self._pressed = False
        if self._long_sent:
            self._long_sent = False
            self._emit("long_release")
            return
        if self.release:
            self._emit("short_release")
        if not self.multi_press:
            self._count = 0

    def idle(self) -> None:
        """multi_press_window passes after the last release."""
        if self.multi_press and self._count:
            count = 0 if self._overflow else self._count
            self._count = 0
            self._overflow = False
            self._emit("multi_press_complete", count)


def run(model: SwitchModel, gesture: str) -> list[str]:
    """Run a gesture: 't' tap, 'L' long press (hold then release), '.' idle."""
    for step in gesture:
        if step == "t":
            model.press()
            model.release_button()
        elif step == "L":
            model.press()
            model.hold()
            model.release_button()
        elif step == ".":
            model.idle()
        else:
            raise ValueError(step)
    return model.events
