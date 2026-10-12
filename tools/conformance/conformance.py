"""Matter conformance expression parser and (three-valued) evaluator.

Operates on the conformance elements of the Matter data model XML
(``mandatoryConform``, ``optionalConform``, ``otherwiseConform`` ...), so no
text notation has to be parsed.

Evaluation result of an element in a context is a :class:`Outcome`:

========  ==============================================================
``M``     mandatory
``O``     optional (``choice`` carries the choice group, if any)
``P``     provisional (not yet part of the spec; never required)
``D``     deprecated (may be present, never required)
``X``     disallowed (must not be present)
``N``     not applicable (the guarding expression is false; must not be
          present, but spec tooling treats presence as a soft violation)
``?``     unknown (an operand could not be determined, e.g. a condition
          or an attribute value that is not available)
``desc``  described in prose only; cannot be evaluated
========  ==============================================================
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


def norm(name: str) -> str:
    """Normalise an element/feature name for comparisons."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


@dataclass
class Context:
    """What an expression can look at."""

    features: set[str] = field(default_factory=set)  # feature codes
    attributes: set[str] | None = None  # normalised names; None = unknown
    commands: set[str] | None = None
    events: set[str] | None = None
    # normalised attribute name -> value (for "NumberOfPrimaries > 0")
    values: dict[str, object] = field(default_factory=dict)
    # condition name (as in the XML) -> bool; absent = unknown
    conditions: dict[str, bool] = field(default_factory=dict)
    revision: int | None = None  # "current" cluster revision

    def feature(self, code: str) -> bool:
        return code in self.features

    def has(self, kind: str, name: str) -> bool | None:
        coll = {
            "attribute": self.attributes,
            "command": self.commands,
            "event": self.events,
        }[kind]
        if coll is None:
            return None
        return norm(name) in coll


@dataclass(frozen=True)
class Outcome:
    kind: str  # M O P D X N ? desc
    choice: str | None = None
    min: int = 1
    more: bool = False
    max: int | None = None

    @property
    def required(self) -> bool:
        return self.kind == "M"


NA = Outcome("N")
UNKNOWN = Outcome("?")


# ----------------------------------------------------------------------------
# Expression terms. Each returns True / False / None (unknown) or, for
# value operands, a number / None.
# ----------------------------------------------------------------------------


def _tri_and(vals):
    if any(v is False for v in vals):
        return False
    if any(v is None for v in vals):
        return None
    return True


def _tri_or(vals):
    if any(v is True for v in vals):
        return True
    if any(v is None for v in vals):
        return None
    return False


def _literal(text):
    if text is None:
        return None
    t = text.strip()
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    try:
        return int(t, 0)
    except ValueError:
        try:
            return float(t)
        except ValueError:
            return t


def eval_term(el: ET.Element, ctx: Context):
    tag = el.tag
    if tag == "feature":
        return ctx.feature(el.get("name"))
    if tag in ("attribute", "command", "event"):
        # As a bare term it means "is supported". In comparisons the
        # attribute is a value operand (see _value).
        return ctx.has(tag, el.get("name"))
    if tag == "condition":
        return ctx.conditions.get(el.get("name"))
    if tag == "andTerm":
        return _tri_and([eval_term(c, ctx) for c in el])
    if tag == "orTerm":
        return _tri_or([eval_term(c, ctx) for c in el])
    if tag == "xorTerm":
        vals = [eval_term(c, ctx) for c in el]
        if any(v is None for v in vals):
            return None
        return sum(1 for v in vals if v) == 1
    if tag == "notTerm":
        v = eval_term(next(iter(el)), ctx)
        return None if v is None else (not v)
    if tag in (
        "equalTerm",
        "greaterTerm",
        "greaterOrEqualTerm",
        "lessTerm",
        "lessOrEqualTerm",
    ):
        a, b = (_value(c, ctx) for c in el)
        if a is None or b is None:
            return None
        try:
            return {
                "equalTerm": a == b,
                "greaterTerm": a > b,
                "greaterOrEqualTerm": a >= b,
                "lessTerm": a < b,
                "lessOrEqualTerm": a <= b,
            }[tag]
        except TypeError:
            return None
    raise ValueError(f"unsupported conformance term <{tag}>")


def _value(el: ET.Element, ctx: Context):
    if el.tag == "literal":
        return _literal(el.get("value"))
    if el.tag == "revision":
        if el.get("value") == "current":
            return ctx.revision
        return _literal(el.get("value"))
    if el.tag == "attribute":
        return ctx.values.get(norm(el.get("name")))
    if el.tag == "field":
        return None
    return eval_term(el, ctx)


def _expr_of(el: ET.Element, ctx: Context):
    """Evaluate the (single) expression child of a conformance element.

    Returns True when there is no expression child (unconditional)."""
    children = list(el)
    if not children:
        return True
    return eval_term(children[0], ctx)


def _choice(el: ET.Element):
    c = el.get("choice")
    if c is None:
        return {}
    more = el.get("more") == "true"
    mn = int(el.get("min", "1"))
    mx = int(el.get("max")) if el.get("max") else (None if more else mn)
    return dict(choice=c, min=mn, more=more, max=mx)


def evaluate(el: ET.Element | None, ctx: Context) -> Outcome:
    """Evaluate a conformance element in ``ctx``."""
    if el is None:
        return UNKNOWN
    tag = el.tag
    if tag == "mandatoryConform":
        v = _expr_of(el, ctx)
        return Outcome("M") if v is True else (NA if v is False else UNKNOWN)
    if tag == "optionalConform":
        v = _expr_of(el, ctx)
        if v is True:
            return Outcome("O", **_choice(el))
        return NA if v is False else UNKNOWN
    if tag == "provisionalConform":
        return Outcome("P")
    if tag == "deprecateConform":
        return Outcome("D")
    if tag == "disallowConform":
        return Outcome("X")
    if tag == "describedConform":
        return Outcome("desc")
    if tag == "otherwiseConform":
        unknown = False
        for child in el:
            r = evaluate(child, ctx)
            if r.kind == "N":
                continue
            if r.kind == "?":
                # An undeterminable branch makes the later ones moot.
                unknown = True
                continue
            if unknown:
                return UNKNOWN
            return r
        return UNKNOWN if unknown else NA
    raise ValueError(f"unsupported conformance element <{tag}>")


CONFORM_TAGS = (
    "mandatoryConform",
    "optionalConform",
    "provisionalConform",
    "deprecateConform",
    "disallowConform",
    "describedConform",
    "otherwiseConform",
)


def conform_of(el: ET.Element) -> ET.Element | None:
    """The conformance child element of a feature/attribute/command/... element."""
    for child in el:
        if child.tag in CONFORM_TAGS:
            return child
    return None
