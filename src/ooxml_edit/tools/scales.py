"""Values styled by a scale given once (production round 36, R31): ``style_by_value`` and
``check``'s ``scale``, for every format.

A **scale** is steps and the values they style, given once by the caller -- in the tool
call that applies it and again in the check that compares the document with it; nothing
is stored in the document::

    {"name": "Score",
     "steps": [{"min": 1, "fill": "C62828", "label": "Low"},
               {"min": 2.5, "fill": "F9A825", "text": "black"},
               {"min": 4, "fill": "2E7D32", "label": "High"}],
     "targets": ["t:3A1F09C2/col2", "{{d:scores/*/score}}"]}

**Steps** (:func:`parse`, refused with the way out where they do not make one scale):

* each step is a ``fill`` (``RRGGBB``) and a ``text`` colour: ``white``, ``black`` or
  ``auto`` (the default) -- white or black, whichever has the higher WCAG 2 contrast ratio
  with the fill, a tie to black (:func:`ooxml_edit.contrast.readable_text`); a ``label``
  names it;
* and what values it takes: **either every step a ``min``** -- a step takes its ``min`` up
  to the next step's (the last: everything above), lowest first; a ``max`` ends a step
  earlier, and the values between it and the next ``min`` are in no step, as given (a gap
  is only ever meant: one without ``max`` cannot happen) -- **or every step a ``value``**:
  the one number, or text (compared without case or surrounding spaces), it takes.  Whole
  numbers skipped between two exact values (1, 2, 4) are refused: a value of 3 would be in
  no step; the way out is the missing step, or ``min``/``max`` ranges for a gap meant;
* at most :data:`MAX_STEPS`; steps listed out of order are refused with the order that
  would do (the steps are numbered as listed: 1 is the lowest).

**A value** is a target's text read as one number in the session's locale (``2,5`` or
``2.5``; thousands separators; ``-``; a percentage as written, ``85 %`` is 85; ``3/5`` and
``3 out of 5`` are 3), or, for a scale of text values, its text.  A text that is not one
number is skipped, saying so.

**Targets** are the format's (each library's :attr:`~.session.DocumentFormat.scale_values`
reads them): a paragraph, a text range, a shape, a table cell; a table's column, every body
cell of it (``<table>/col2``, from 0; a header row left out); and the values
written by reference (``{{d:scores/*/score}}``: every value a text tool wrote from such a
reference and still where it was written; ``*`` stands for one part of the reference,
``{{*}}`` for every one).  The step follows the value as it is when the call runs.

**Applying** (:func:`apply`, a library's ``style_by_value`` handler): the format reads each
target's value and the step it is styled with now; the steps due are written by the
library's ``write`` (a document with styles: a character style per step, named from the
scale -- ``Score 1 Low`` -- made once and reused, and cell shading; a format without
character styles: the fill and the text colour, directly).  One call, one undo step; ``preview`` writes nothing.  The reply lists
each target: the value read, the step, ``was`` where it had another, or why it was
skipped; a value already styled with its step is ``unchanged`` (no change, no error).

**The check** (:func:`with_scale`, around every kind's ``check``): ``check(scale=...)``
lists the values styled with another step than their value's (``wrong``: a value changed
after it was styled, a step's colours changed) and those with none (``none``), with the
one call that fixes them; scoped as the check is (the format's scope arguments),
capped (:data:`CHECK_LISTED`, ``left_out``).  Never on by default: the ``scale`` asks for it.

**Labels** (0.87.0): a scale with ``"show": "label"`` writes each step's ``label`` in
place of the value it stands for -- the text then reads ``Low`` (in the step's colours),
not ``1,4`` -- and the document keeps, where the label is, the value and where it came
from (a library's :attr:`ScaleValue.kept`; :func:`ooxml_edit.provenance.labels_after_edit`
says how such a record follows edits, one of the label's own text included).  Every step
needs a label, and no two the same.  The value a label stands for is, in order:

* the text, where it reads as a value (a number, or a step's ``value`` text): a value
  typed, or written by reference (its reference is kept: :attr:`ScaleValue.source`) --
  also over a label written before;
* else the value the label's record names: its ``source`` read again now when the
  session can (a dataset, a table of a document it holds; :meth:`CallContext.fact`) --
  so a record's value changed since is seen -- or else the value recorded;
* a step's label with no record names no value: it is reported (``unrecorded``), never
  guessed -- so are several labels, one per line, with none (a table cell merged with
  another: ``label_of`` lists their steps).

``style_by_value`` writes the label (keeping the text's own formatting, the step's
style or colours over it) and the record; ``unchanged`` where the label, the style and the
record are already right.  ``check(scale=...)`` adds ``labels``: the texts that are not their
value's label -- ``why``: it shows the value, the value changed (``recorded 1,4, now
2,7``), the label was edited (``written 'Low'``), the steps changed -- and ``unrecorded``;
``fix`` writes them all again.  Exact text steps (``value`` texts) work as before.
"""

from __future__ import annotations

import fnmatch
import functools
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..contrast import BLACK, TEXT_MINIMUM, WHITE, hex_colour, ratio, readable_text
from .numbers import _parse
from .results import Result, ToolError

#: The tool that applies a scale.
TOOL = "style_by_value"
#: ``check``'s parameter that asks for the fact.
PARAM = "scale"
#: Steps in one scale at most.
MAX_STEPS = 12
#: Targets in one scale at most (a table column or a reference counts once).
MAX_TARGETS = 50
#: Targets a ``style_by_value`` reply lists; ``left_out`` counts the rest.
LISTED = 40
#: Values a check lists (wrong and none together), and skipped ones.
CHECK_LISTED = 20
SKIPPED_LISTED = 10
#: The name a scale's styles take when it gives none.
DEFAULT_NAME = "Step"
#: A step's text colours, as the tools name them.
TEXT_COLOURS = {"white": WHITE, "black": BLACK}

#: What a scale's text shows: its value (as written), or its step's label.
SHOW = ("value", "label")
#: ``show`` as an agent may send it (rule 1), and what it means.
_SHOW_SAID = {"": "value", "value": "value", "values": "value", "label": "label",
              "labels": "label"}

#: Said with a check's fact: how values are read and compared.
NOTE = ("scale: each value's step from its text as it is now (a number in the locale, or the "
        "text); wrong: styled with another step; none: with no step of this scale. fix restyles them")

_COLUMN = re.compile(r"(?P<table>.+)/col(?P<column>\d+)")
_REFERENCE = re.compile(r"\{\{\s*(?P<pattern>[^{}]*?)\s*\}\}")
_SIGN = "-−‒–"
_OUT_OF = re.compile(r"\s*(?:/|out\s+of)\s*\d+(?:[.,]\d+)?\s*$", re.IGNORECASE)
_PERCENT = re.compile(r"\s*[%‰]\s*$")


# -- the scale -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One step: ``index`` from 0 (``number`` from 1, as styles are named), its ``fill``
    and ``text`` colours ``RRGGBB`` (``text`` chosen: ``rule`` says how), the WCAG 2
    ``contrast`` between them, and what it takes -- ``min`` (and ``max``), or ``value``
    (``number`` when it is one)."""

    index: int
    fill: str
    text: str
    rule: str
    contrast: float
    label: str | None = None
    min: float | None = None
    max: float | None = None
    value: str | None = None
    number: float | None = None

    @property
    def n(self) -> int:
        return self.index + 1

    def takes(self) -> str:
        if self.min is not None:
            return f"{_num(self.min)}" + (f" to {_num(self.max)}" if self.max is not None else " up")
        return repr(self.value)

    def brief(self, style: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"step": self.n}
        if self.label:
            out["label"] = self.label
        if style:
            out["style"] = style
        out.update(fill=self.fill, text=self.text, contrast=self.contrast)
        return out


@dataclass(frozen=True)
class Scale:
    """A scale as :func:`parse` read it; ``given`` is the argument as the caller gave it
    (for the fix a check offers), ``notes`` what the reading has to say (a gap meant, a
    step's colours below WCAG's 4.5:1)."""

    name: str
    steps: tuple[Step, ...]
    targets: tuple[str, ...]
    by: str                      # "range", "value" or "text"
    given: Mapping[str, Any] = field(default_factory=dict, compare=False)
    notes: tuple[str, ...] = ()
    show: str = "value"          # "value" or "label" (the module's notes)

    @property
    def labels(self) -> bool:
        """Whether the scale's texts show its steps' labels."""
        return self.show == "label"

    def step_of_label(self, text: str) -> Step | None:
        """The step whose label ``text`` is (case and surrounding spaces aside), or ``None``."""
        wanted = _folded(text)
        return next((s for s in self.steps if s.label and _folded(s.label) == wanted), None)

    def value_of(self, text: str, locale: Any) -> Any:
        """``text`` as a value of this scale -- a number, or for a scale of texts a step's
        text -- or ``None`` when it is none."""
        if self.by == "text":
            wanted = _folded(text)
            return next((s.value for s in self.steps if s.value is not None
                         and _folded(s.value) == wanted), None)
        return read_value(text, locale)

    def style_name(self, step: Step) -> str:
        """The name of a step's style: the scale's name, the step's number, its label."""
        return f"{self.name} {step.n}" + (f" {step.label}" if step.label else "")

    def step_of_style(self, name: str | None) -> int | None:
        """The index of the step a style named ``name`` is for -- this scale's name and a
        step's number, whatever label follows (a style kept from before a label changed
        is still that step's) -- or ``None``."""
        if not name:
            return None
        match = re.fullmatch(re.escape(self.name) + r" (\d+)(?: .*)?", name.strip(), re.IGNORECASE)
        if match is None:
            return None
        number = int(match.group(1))
        return number - 1 if 1 <= number <= len(self.steps) else None

    def due(self, text: str, locale: Any) -> tuple[Step | None, str | None]:
        """The step ``text`` (a value as written) is due, or ``None`` and why not."""
        if self.by == "text":
            wanted = _folded(text)
            for step in self.steps:
                if step.value is not None and _folded(step.value) == wanted:
                    return step, None
            return None, (f"{_quote(text)} is no step's text; steps take "
                          + ", ".join(repr(s.value) for s in self.steps))
        number = read_value(text, locale)
        if number is None:
            return None, f"{_quote(text)} is not one number"
        return self.due_number(number)

    def due_value(self, value: Any) -> tuple[Step | None, str | None]:
        """The step a value -- a number, or a text for a scale of texts, as a label's record
        keeps it -- is due, or ``None`` and why not."""
        if self.by == "text" or isinstance(value, str):
            return self.due(str(value), None) if self.by == "text" else \
                (None, f"{_quote(str(value))} is not one number")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"{value!r} is not one number"
        return self.due_number(float(value))

    def due_number(self, number: float) -> tuple[Step | None, str | None]:
        """The step a number is due, or ``None`` and why not."""
        if self.by == "value":
            for step in self.steps:
                if step.number is not None and math.isclose(step.number, number, rel_tol=0, abs_tol=1e-9):
                    return step, None
            return None, (f"{_num(number)} is no step's value; steps take "
                          + ", ".join(_num(s.number) for s in self.steps if s.number is not None))
        first = self.steps[0]
        if first.min is not None and number < first.min:
            return None, f"{_num(number)} is below the first step's min {_num(first.min)}"
        chosen = first
        for step in self.steps:
            if step.min is not None and step.min <= number:
                chosen = step
        if chosen.max is not None and number > chosen.max:
            following = self.steps[chosen.index + 1] if chosen.index + 1 < len(self.steps) else None
            where = (f"between step {chosen.n}'s max {_num(chosen.max)} and step {following.n}'s min "
                     f"{_num(following.min)}" if following is not None and following.min is not None
                     else f"above the last step's max {_num(chosen.max)}")
            return None, f"{_num(number)} is in no step: {where}"
        return chosen, None


def _folded(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _num(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:g}" if float(value).is_integer() or abs(value) >= 1e-4 else repr(value)


def _quote(text: str, limit: int = 40) -> str:
    text = " ".join(text.split())
    return repr(text if len(text) <= limit else text[:limit - 1] + "…")


def read_value(text: str, locale: Any) -> float | None:
    """``text`` read as one number in ``locale`` (the module's notes), or ``None``."""
    body = " ".join(str(text).replace(" ", " ").replace(" ", " ").split())
    body = _OUT_OF.sub("", body)
    body = _PERCENT.sub("", body)
    sign = 1.0
    if body[:1] in _SIGN:
        sign, body = -1.0, body[1:].lstrip()
    elif body[:1] == "+":
        body = body[1:].lstrip()
    if not body or not body[0].isdigit():
        if body[:1] in ".," and body[1:].isdigit():
            body = "0" + body
        else:
            return None
    group = getattr(locale, "thousands_separator", ",")
    if group in "   ":
        body = body.replace(" ", group)
    if not re.fullmatch(r"\d+(?:[.,'’   ]\d+)*", body):
        return None
    parsed = _parse(body, locale)
    if parsed is None:
        return None
    return sign * float(parsed[0])


def _refused(message: str, *, example: bool = True) -> ToolError:
    return ToolError("invalid_arguments", message, field=PARAM, details={"example": EXAMPLE} if example else None)


#: The shape of a scale, in a refusal of one.
EXAMPLE = {"name": "Score",
           "steps": [{"min": 1, "fill": "C62828", "label": "Low"},
                     {"min": 3, "fill": "F9A825", "text": "black", "label": "Medium"},
                     {"min": 4, "fill": "2E7D32", "label": "High"}],
           "targets": ["<a table>/col2", "{{d:<dataset>/*/<column>}}"]}


def parse(given: Any) -> Scale:
    """The scale ``given`` describes (the module's notes), or ``invalid_arguments`` saying
    what is wrong and the way out, with an example scale in ``details``."""
    if not isinstance(given, Mapping):
        raise _refused("scale is an object: {name, steps: [{min or value, fill, text, label}], targets}")
    unknown = sorted(set(given) - {"name", "steps", "targets", "show"})
    if unknown:
        raise _refused(f"scale has no {', '.join(unknown)}: it takes name, steps, targets and show")
    shown = given.get("show")
    show = _SHOW_SAID.get(str(shown).strip().lower() if shown is not None else "")
    if show is None:
        raise _refused(f"scale show {shown!r}: value (the text is the value, the default) or label "
                       "(the text becomes its step's label)", example=False)
    name = given.get("name")
    name = DEFAULT_NAME if name in (None, "") else str(name).strip()
    if not name or len(name) > 40 or re.search(r"[\d]$", name):
        raise _refused(f"scale name {given.get('name')!r}: 1 to 40 characters, not ending in a digit "
                       "(the styles are named '<name> <step> <label>')")
    steps = given.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, str) or not steps:
        raise _refused("scale steps: a list of at least one step, lowest first")
    if len(steps) > MAX_STEPS:
        raise _refused(f"{len(steps)} steps: at most {MAX_STEPS} in one scale", example=False)
    targets = given.get("targets")
    if isinstance(targets, str):
        targets = [targets]
    if not isinstance(targets, Sequence) or not targets or not all(isinstance(t, str) and t.strip()
                                                                   for t in targets):
        raise _refused("scale targets: the values to style, a list of addresses -- a paragraph, a "
                       "range, a shape or a cell, a table column (/col2), {{...}} references")
    if len(targets) > MAX_TARGETS:
        raise _refused(f"{len(targets)} targets: at most {MAX_TARGETS}; a table column (/col2) or a "
                       "reference with * names many values at once", example=False)
    raw: list[Mapping[str, Any]] = []
    for k, step in enumerate(steps):
        if not isinstance(step, Mapping):
            raise _refused(f"steps[{k}] is an object: {{min or value, fill, text, label}}")
        extra = sorted(set(step) - {"min", "max", "value", "fill", "text", "label"})
        if extra:
            raise _refused(f"steps[{k}] has no {', '.join(extra)}: a step takes min (and max) or value, "
                           "fill, text and label")
        raw.append(step)
    kinds = ["min" if s.get("min") is not None else "value" if s.get("value") not in (None, "") else None
             for s in raw]
    missing = [k for k, kind in enumerate(kinds) if kind is None]
    if missing:
        raise _refused(f"steps[{missing[0]}] says no values: give min (a step from there up to the next "
                       "step's min) or value (one number or text)")
    if len(set(kinds)) > 1:
        raise _refused("steps mix min and value: give every step a min (ranges), or every step a value "
                       "(exact numbers or texts)")
    notes: list[str] = []
    out: list[Step] = []
    for k, step in enumerate(raw):
        given_fill = step.get("fill")
        fill = hex_colour(given_fill) if isinstance(given_fill, str) else None
        if fill is None:
            raise _refused(f"steps[{k}].fill {step.get('fill')!r} is not a colour: give RRGGBB, e.g. 2E7D32")
        rule = (step.get("text") or "auto")
        rule = str(rule).strip().lower()
        if rule == "auto":
            text, contrast = readable_text(fill)
        elif rule in TEXT_COLOURS:
            text = TEXT_COLOURS[rule]
            contrast = round(ratio(text, fill), 2)
        elif hex_colour(rule) in (WHITE, BLACK):          # FFFFFF read as white (rule 1)
            text = hex_colour(rule) or WHITE
            rule = "white" if text == WHITE else "black"
            contrast = round(ratio(text, fill), 2)
        else:
            raise _refused(f"steps[{k}].text {step.get('text')!r}: white, black or auto (the one that "
                           "contrasts more with the fill)", example=False)
        label = step.get("label")
        label = None if label in (None, "") else " ".join(str(label).split())
        if label is not None and len(label) > 40:
            raise _refused(f"steps[{k}].label: at most 40 characters", example=False)
        low = _number(step.get("min"), f"steps[{k}].min")
        high = _number(step.get("max"), f"steps[{k}].max")
        if high is not None and low is None:
            raise _refused(f"steps[{k}].max with no min: a step's range is min to max; or give value")
        value = step.get("value")
        value = None if value is None else (" ".join(str(value).split()) if not isinstance(value, bool)
                                            else str(value))
        out.append(Step(k, fill, text, rule, contrast, label, low, high, value))
        if contrast < TEXT_MINIMUM:
            notes.append(f"step {k + 1}: {rule} text on {fill} has contrast {contrast}:1, below WCAG's "
                         f"{TEXT_MINIMUM}:1 for text" + ("" if rule == "auto" else "; text auto chooses "
                                                        "the higher of white and black"))
    by = "range" if kinds[0] == "min" else "value"
    if by == "range":
        for a, b in zip(out, out[1:]):
            if b.min is not None and a.min is not None and b.min <= a.min:
                order = sorted(out, key=lambda s: s.min if s.min is not None else 0.0)
                raise _refused(f"steps are listed lowest first, numbered as listed: step {b.n}'s min "
                               f"{_num(b.min)} is not above step {a.n}'s {_num(a.min)}; in order the mins "
                               f"are {[s.min for s in order]}", example=False)
        for step in out:
            if step.max is not None and step.min is not None and step.max < step.min:
                raise _refused(f"step {step.n}: max {_num(step.max)} is below its min {_num(step.min)}",
                               example=False)
        for a, b in zip(out, out[1:]):
            if a.max is not None and b.min is not None:
                if a.max >= b.min:
                    raise _refused(f"step {a.n}'s max {_num(a.max)} reaches step {b.n}'s min {_num(b.min)}: "
                                   "steps overlap; end it below the next min, or leave max out", example=False)
                notes.append(f"values above {_num(a.max)} and below {_num(b.min)} are in no step (step "
                             f"{a.n}'s max): skipped")
    else:
        numbers = [_as_number(s.value) for s in out]
        if all(n is not None for n in numbers):
            out = [Step(s.index, s.fill, s.text, s.rule, s.contrast, s.label, None, None, s.value, n)
                   for s, n in zip(out, numbers)]
            values = [n for n in numbers if n is not None]
            if len(set(values)) < len(values):
                raise _refused("two steps take the same value: each value is one step's", example=False)
            if values != sorted(values):
                raise _refused(f"steps are listed lowest first, numbered as listed: the values are {values}, "
                               f"in order {sorted(values)}", example=False)
            if all(float(v).is_integer() for v in values):
                skipped = [int(v) + 1 for v, w in zip(values, values[1:]) if w - v > 1]
                if skipped:
                    raise _refused(f"no step takes {_num(skipped[0])}: whole values {', '.join(_num(v) for v in values)}"
                                   " skip it. Add its step, or give min/max ranges where a gap is meant",
                                   example=False)
        else:
            by = "text"
            seen: set[str] = set()
            for step in out:
                key = (step.value or "").casefold()
                if key in seen:
                    raise _refused(f"two steps take {step.value!r}: each text is one step's", example=False)
                seen.add(key)
    if show == "label":
        unnamed = [s.n for s in out if not s.label]
        if unnamed:
            raise _refused(f"show label writes each step's label, and step {unnamed[0]} has none: give "
                           "every step a label", example=False)
        seen_labels: dict[str, int] = {}
        for step in out:
            assert step.label is not None
            twin = seen_labels.setdefault(_folded(step.label), step.n)
            if twin != step.n:
                raise _refused(f"steps {twin} and {step.n} are both labelled {step.label!r}: with show "
                               "label a label names one step", example=False)
    return Scale(name, tuple(out), tuple(t.strip() for t in targets), by, dict(given), tuple(notes),
                 show)


def _number(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        found = _as_number(str(value))
        if found is None:
            raise _refused(f"{where} {value!r} is not a number", example=False)
        return found
    return float(value)


def _as_number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace("−", "-")
    try:
        return float(text)
    except ValueError:
        return None


# -- what a format reads -------------------------------------------------------------------------


@dataclass
class ScaleValue:
    """One value a format reads at a target (:attr:`~.session.DocumentFormat.scale_values`).

    ``target`` -- where the value is, as the format's tools take it (a text range, a cell,
    a shape); ``text`` -- the value as
    written; ``given`` -- the target as the scale names it, where that named many values (a
    column, a reference); ``now`` -- the index of the step it is styled with now (``None``:
    none of this scale's), ``exact`` -- whether it is styled as that step says, and
    ``differs`` what is not (``"fill FFC000"``); ``skip`` -- why it cannot be styled (no
    such cell, a header row), the rest then unread; ``where`` -- what a reader is told of
    its place (``{"page": 3}``); ``handle`` -- the format's own, for its writer.

    For labels (0.87.0): ``kept`` -- the label record the document keeps where the text is
    (:func:`label_record`'s fields, as the format read them; ``None``: none); ``source`` --
    the reference the text was written from, where it was (a text fact's); ``record`` --
    set by :func:`apply` for the writer: the record to keep with the label it writes."""

    target: str
    text: str = ""
    given: str | None = None
    now: int | None = None
    exact: bool = True
    differs: str | None = None
    skip: str | None = None
    where: Mapping[str, Any] = field(default_factory=dict)
    handle: Any = None
    kept: Mapping[str, Any] | None = None
    source: str | None = None
    record: dict[str, Any] | None = None


def column_target(target: str) -> tuple[str, int] | None:
    """``(table, column)`` for a table column target (``<table>/col2``), else
    ``None``."""
    match = _COLUMN.fullmatch(target.strip())
    return (match.group("table"), int(match.group("column"))) if match else None


def reference_target(target: str) -> str | None:
    """The pattern of a ``{{...}}`` target (``d:scores/*/score``), else ``None``."""
    match = _REFERENCE.fullmatch(target.strip())
    return match.group("pattern") if match else None


def reference_matches(wanted: str, source: str) -> bool:
    """Whether a value written from ``source`` (a text fact's reference, ``d:scores/A/score``)
    is one ``wanted`` names: the same reference, a format after ``:`` aside, case aside;
    ``*`` stands for any one part between ``/``, a ``wanted`` of ``*`` alone for every
    reference."""
    wanted, source = _bare(wanted), _bare(source)
    if wanted in ("*", ""):
        return True
    parts, have = wanted.split("/"), source.split("/")
    if len(parts) != len(have):
        return False
    return all(fnmatch.fnmatchcase(h, p) for p, h in zip(parts, have))


def _bare(reference: str) -> str:
    """A reference without its format (``d:x/a/b:0.0`` -> ``d:x/a/b``), folded."""
    text = reference.strip()
    head, sep, rest = text.partition(":")              # the prefix's colon: d:, t:
    if sep and "/" in rest:
        last = rest.rsplit("/", 1)
        tail = last[1].split(":", 1)[0]
        rest = f"{last[0]}/{tail}"
    return (head + sep + rest).casefold()


def values_of(call: Any, scale: Scale, scope: Mapping[str, Any]) -> list[ScaleValue]:
    """The values of ``scale``'s targets in the call's document, by its format's
    :attr:`~.session.DocumentFormat.scale_values` (``scope``: the check's arguments that
    narrow it); ``invalid_arguments`` when the format reads none."""
    entry = call.entry
    fmt = call.session.formats.get(entry.kind) if entry is not None else None
    hook = getattr(fmt, "scale_values", None)
    if hook is None:
        kinds = sorted(kind for kind, other in call.session.formats.items()
                       if getattr(other, "scale_values", None) is not None)
        raise ToolError("invalid_arguments", f"a scale styles values in {' and '.join(kinds) or 'no'} "
                        f"documents here, not a {entry.kind if entry else 'call without a document'}",
                        field=PARAM)
    found = list(hook(call, scale, scope))
    for item in found:
        if not isinstance(item, ScaleValue):
            raise TypeError(f"the {entry.kind} scale_values hook gave {type(item).__name__}, not ScaleValue")
    return found


# -- labels --------------------------------------------------------------------------------------


def label_record(scale: Scale, step: Step, value: Any, source: str | None) -> dict[str, Any]:
    """What a document keeps with a step's label written in place of a value (the format
    adds where: ``p``, ``at``, ``text``): the ``label``, the ``value`` it stands for (a
    number, or a scale of texts' text), the ``source`` it came from (a reference, when it
    did), the scale's ``name`` and the ``step``'s number."""
    out: dict[str, Any] = {"label": step.label, "value": _plain(value)}
    if source:
        out["source"] = source
    out.update(scale=scale.name, step=step.n)
    return out


def _plain(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _said(value: Any, locale: Any) -> str:
    """A value as a reply says it: a number in the locale (``1,4``), a text as it is."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    text = _num(float(value))
    decimal = getattr(locale, "decimal_separator", ".")
    return text.replace(".", decimal) if "e" not in text else text


def _same_value(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
            and not isinstance(b, bool):
        return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-12)
    return a is not None and b is not None and _folded(str(a)) == _folded(str(b))


def _same_text(a: str | None, b: str | None) -> bool:
    """Whether a text reads as a label: the same, spaces around and between aside."""
    return a is not None and b is not None and " ".join(a.split()) == " ".join(b.split())


@dataclass
class Reading:
    """A target's value as :func:`read` found it: ``value`` (a number, or a text) and
    ``said`` as a reply says it; ``step`` it is due, or ``why`` not; ``label`` -- its text
    is a label, the value its record's; ``source`` its reference; ``changed`` -- the
    source's value now is not the one recorded (``recorded 1,4, now 2,7``); ``recorded`` --
    the value is the record's, its source not read now (``unread`` why);
    ``unrecorded`` -- the text is this step's label, and no value is recorded for it."""

    value: Any = None
    said: str = ""
    step: Step | None = None
    why: str | None = None
    label: bool = False
    source: str | None = None
    changed: str | None = None
    recorded: bool = False
    unread: str | None = None
    unrecorded: Step | None = None
    #: Every step whose label the text holds, one per line, when it holds several.
    several: tuple[Step, ...] = ()


def read(call: Any, scale: Scale, value: ScaleValue) -> Reading:
    """The value ``value``'s target stands for and its step (the module's notes)."""
    locale = getattr(call, "locale", None)
    text = value.text
    if not scale.labels:
        step, why = scale.due(text, locale)
        if step is None and value.kept:
            kept_step = value.kept.get("step")
            why = f"{why}; it is step {kept_step}'s label: give the scale show label"
        return Reading(scale.value_of(text, locale), text, step, why, source=value.source)
    shown = scale.value_of(text, locale)
    if shown is not None:
        step, why = scale.due_value(shown)
        return Reading(shown, text, step, why, source=value.source)
    kept = value.kept
    if kept is not None and kept.get("value") is not None:
        recorded = kept.get("value")
        source = kept.get("source") if isinstance(kept.get("source"), str) else None
        reading = Reading(recorded, _said(recorded, locale), label=True, source=source)
        if source is not None:
            now, unread = _source_value(call, scale, source)
            if now is None:
                reading.recorded, reading.unread = True, unread
            else:
                if not _same_value(now, recorded):
                    reading.changed = f"recorded {_said(recorded, locale)}, now {_said(now, locale)}"
                reading.value, reading.said = now, _said(now, locale)
        reading.step, reading.why = scale.due_value(reading.value)
        return reading
    step = scale.step_of_label(text)
    if step is not None:
        return Reading(said=text, label=True, unrecorded=step,
                       why=f"{_quote(text)} is step {step.n}'s label, and no value is recorded for it: "
                       "write its value there (typed, or by reference {{d:...}}), then label it")
    lines = [line for line in text.split("\n") if line.strip()]
    steps = [scale.step_of_label(line) for line in lines]
    if len(lines) > 1 and all(s is not None for s in steps):
        held = tuple(s for s in steps if s is not None)
        return Reading(said=text, label=True, unrecorded=held[0], several=held,
                       why=f"{_quote(text)} holds the labels of steps "
                       f"{', '.join(str(s.n) for s in held)}, one per line, and no value is recorded "
                       "for it: write its one value there (typed, or by reference {{d:...}}), then "
                       "label it")
    return Reading(said=text, why=f"{_quote(text)} is not one number, nor a step's label")


def _source_value(call: Any, scale: Scale, source: str) -> tuple[Any, str | None]:
    """The value ``source`` (a reference, without braces) names now, as this scale reads
    it, or ``None`` and why it could not be read."""
    fact = getattr(call, "fact", None)
    if fact is None:
        return None, "no session to read it in"
    try:
        text, _record = fact(source)
    except ToolError as error:
        return None, error.message
    found = scale.value_of(text, getattr(call, "locale", None))
    if found is None:
        return None, f"{source} now reads {_quote(text)}, no value of this scale"
    return found, None


def reads_values(arguments: Mapping[str, Any]) -> bool:
    """Whether a call's ``scale`` shows labels, whose values may be read again from their
    sources: the dispatcher then holds the documents that have values to read
    (:attr:`~.registry.Tool.value_reads`)."""
    given = arguments.get(PARAM)
    shown = given.get("show") if isinstance(given, Mapping) else None
    return isinstance(shown, str) and _SHOW_SAID.get(shown.strip().lower()) == "label"


# -- applying ------------------------------------------------------------------------------------


#: ``write(call, scale, plan, preview) -> Result``: a library writes each ``(value, step)``
#: of ``plan`` (nothing when ``preview``), its result's ``data`` saying what it made
#: (``styles``) -- the rest of the reply is :func:`apply`'s.  A scale that shows labels:
#: the library writes ``step.label`` in place of the value's text, keeping the text's own
#: formatting, and keeps ``value.record`` (:func:`label_record`) where the label is.
Writer = Callable[[Any, Scale, list[tuple[ScaleValue, Step]], bool], Result]


def _entry(value: ScaleValue) -> dict[str, Any]:
    out: dict[str, Any] = {"target": value.target}
    if value.given and value.given != value.target:
        out["from"] = value.given
    return out


def _label_right(value: ScaleValue, reading: Reading, step: Step) -> bool:
    """Whether a label scale's text is its value's label, and its record says so."""
    kept = value.kept
    return (_same_text(value.text, step.label) and kept is not None
            and _same_value(kept.get("value"), reading.value)
            and (kept.get("source") or None) == (reading.source or None))


def apply(call: Any, given: Any, preview: bool, write: Writer) -> Result:
    """``style_by_value`` for a library: read, choose, write (the module's notes)."""
    scale = parse(given)
    values = values_of(call, scale, {})
    plan: list[tuple[ScaleValue, Step]] = []
    entries: list[dict[str, Any]] = []
    unchanged = skipped = 0
    for value in values:
        entry = _entry(value)
        if value.skip:
            entries.append({**entry, "skipped": value.skip})
            skipped += 1
            continue
        reading = read(call, scale, value)
        step = reading.step
        entry["value"] = reading.said
        if scale.labels and reading.label:
            entry["shows"] = value.text
        if reading.recorded:
            entry["recorded"] = f"{reading.source} not read now ({reading.unread}): the value recorded"
        if step is None:
            entries.append({**entry, "skipped": reading.why})
            skipped += 1
            continue
        entry["step"] = step.n
        if scale.labels:
            entry["label"] = step.label
        text_right = not scale.labels or _label_right(value, reading, step)
        if value.now == step.index and value.exact and text_right:
            entries.append({**entry, "unchanged": True})
            unchanged += 1
            continue
        if value.now is not None and value.now != step.index:
            entry["was"] = value.now + 1
        elif value.differs:
            entry["was"] = value.differs
        if reading.changed:
            entry["changed"] = reading.changed
        if scale.labels:
            value.record = label_record(scale, step, reading.value, reading.source)
        entries.append(entry)
        plan.append((value, step))
    result = write(call, scale, plan, preview) if plan or preview else Result()
    written = dict(result.data) if isinstance(result.data, Mapping) else {}
    data: dict[str, Any] = {}
    data["steps"] = written.pop("steps", None) or [s.brief() for s in scale.steps]
    data.update(written)
    data["targets"] = entries[:LISTED]
    if len(entries) > LISTED:
        data["left_out"] = len(entries) - LISTED
    if scale.notes:
        result.warnings.extend(n for n in scale.notes if n not in result.warnings)
    verb = ("would label" if preview else "labelled") if scale.labels else \
        ("would style" if preview else "styled")
    result.summary = (f"{scale.name}: {verb} {len(plan)} value(s), {unchanged} already in their step, "
                      f"{skipped} skipped")
    if not plan:
        result.warnings.append("nothing to change: every value is in its step or skipped"
                               if values else "the targets hold no values")
    result.data = data
    return result


# -- the check -----------------------------------------------------------------------------------


def _label_why(value: ScaleValue, reading: Reading, step: Step) -> str:
    """Why a label scale's text is not its value's label (:func:`report`)."""
    if not reading.label:
        return "shows its value, not its label" if value.kept is None else \
            "shows a value typed over its label"
    if reading.changed:
        return f"its value changed: {reading.changed}"
    kept = value.kept or {}
    written = kept.get("label")
    if isinstance(written, str) and not _same_text(value.text, written):
        return f"edited: written {_quote(written)}"
    if isinstance(written, str) and written != step.label:
        return f"the steps changed: written as step {kept.get('step')}'s label {_quote(written)}"
    return "not its value's label"


def report(call: Any, given: Any, scope: Mapping[str, Any]) -> dict[str, Any]:
    """``check``'s fact for ``scale`` (the module's notes)."""
    scale = parse(given)
    values = values_of(call, scale, scope)
    wrong: list[dict[str, Any]] = []
    none: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    unrecorded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    recorded = 0
    right = 0
    for value in values:
        entry = _entry(value)
        entry.update(value.where)
        if value.skip:
            skipped.append({**entry, "why": value.skip})
            continue
        reading = read(call, scale, value)
        step = reading.step
        if reading.unrecorded is not None:
            unrecorded.append({**entry, "shows": value.text,
                               "label_of": [s.n for s in reading.several] if reading.several
                               else reading.unrecorded.n})
            continue
        entry["value"] = reading.said
        if scale.labels and reading.label:
            entry["shows"] = value.text
        if step is None:
            skipped.append({**entry, "why": reading.why})
            continue
        recorded += reading.recorded
        styled = value.now == step.index and value.exact
        text_right = not scale.labels or _label_right(value, reading, step)
        if styled and text_right:
            right += 1
        if styled:
            pass
        elif value.now is None:
            none.append({**entry, "due": step.n})
        else:
            item = {**entry, "step": value.now + 1, "due": step.n}
            if value.now == step.index and value.differs:
                item["differs"] = value.differs
            wrong.append(item)
        if not text_right:
            labels.append({**entry, "shows": value.text, "label": step.label, "due": step.n,
                           "why": _label_why(value, reading, step)})
    found = wrong + none + labels + unrecorded
    listed = found[:CHECK_LISTED]
    kept = {id(e) for e in listed}
    data: dict[str, Any] = {"checked": len(values) - len(skipped), "right": right,
                            "wrong": [e for e in wrong if id(e) in kept],
                            "none": [e for e in none if id(e) in kept]}
    if scale.labels:
        data["labels"] = [e for e in labels if id(e) in kept]
        data["unrecorded"] = [e for e in unrecorded if id(e) in kept]
    left = len(found) - len(listed)
    if left > 0:
        data["left_out"] = left
    if skipped:
        data["skipped"] = skipped[:SKIPPED_LISTED]
        if len(skipped) > SKIPPED_LISTED:
            data["skipped_left_out"] = len(skipped) - SKIPPED_LISTED
    if wrong or none or labels:
        data["fix"] = _fix(call, scale, [e["target"] for e in wrong + none + labels])
    notes = list(scale.notes)
    if unrecorded:
        notes.append("unrecorded: a step's label with no value kept for it, so its step cannot be "
                     "checked; write its value there (typed, or by reference {{d:...}}), then "
                     "style_by_value with show label")
    if recorded:
        notes.append(f"{recorded} label(s) checked against the value recorded: their source "
                     "could not be read now")
    if notes:
        data["notes"] = notes
    return data


def _fix(call: Any, scale: Scale, targets: list[str]) -> dict[str, Any]:
    """The one ``style_by_value`` call that restyles ``targets``: those addresses, or --
    more than a scale takes -- the scale's own targets (a value in its step is left as it
    is)."""
    given = dict(scale.given)
    unique = list(dict.fromkeys(targets))
    if len(unique) <= MAX_TARGETS:
        given["targets"] = unique
    doc = call.entry.doc_id if call.entry is not None else None
    return {"tool": TOOL, "arguments": {"doc": doc, PARAM: given}}


def summary(data: Mapping[str, Any]) -> str:
    wrong = data["checked"] - data["right"]
    return (f"scale: {data['right']} of {data['checked']} value(s) in their step, {wrong} not"
            + (f", {len(data.get('skipped') or ()) + data.get('skipped_left_out', 0)} skipped"
               if data.get("skipped") else ""))


def with_scale(handler: Callable[..., Any], scope: Sequence[str],
               asks: Sequence[str]) -> Callable[..., Any]:
    """``check``'s handler for a kind, ``scale`` done here for every kind: the fact is added
    to the kind's result as ``scale``; the kind's handler never sees the argument, and is
    not run when nothing else is asked of it (no ``include``, none of ``asks``)."""

    @functools.wraps(handler)
    def run(call: Any, **arguments: Any) -> Any:
        given = arguments.pop(PARAM, None)
        if given is None:
            return handler(call, **arguments)
        narrowed = {name: arguments[name] for name in scope if arguments.get(name) is not None}
        data = report(call, given, narrowed)
        said = summary(data)
        if not arguments.get("include") and not any(arguments.get(name) not in (None, False, [], {})
                                                    for name in asks):
            return Result(summary=said, data={PARAM: data})
        returned = handler(call, **arguments)
        if isinstance(returned, Result):
            if isinstance(returned.data, dict):
                returned.data[PARAM] = data
            returned.summary = f"{returned.summary}; {said}" if returned.summary else said
            return returned
        if isinstance(returned, dict):
            returned[PARAM] = data
        return returned

    return run


__all__ = ["CHECK_LISTED", "DEFAULT_NAME", "EXAMPLE", "LISTED", "MAX_STEPS", "MAX_TARGETS", "NOTE",
           "PARAM", "Reading", "SHOW", "Scale", "ScaleValue", "Step", "TOOL", "Writer", "apply",
           "column_target", "label_record", "parse", "read", "read_value", "reads_values",
           "reference_matches", "reference_target", "report", "summary", "values_of",
           "with_scale"]
