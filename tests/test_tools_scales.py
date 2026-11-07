"""Values styled by a scale given once: ``style_by_value`` and ``check``'s ``scale`` (round 36).

The toy: a board (kind ``pptx``) of labelled values, each with the step it is styled with
now; its ``scale_values`` hook reads a scale's targets (an address, a column ``col:<n>``,
``{{...}}`` references to the values it says were written by reference) and its writer
sets the steps.  Every value is made up.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from ooxml_common.locales import Locale

from ooxml_edit.contrast import luminance, ratio, readable_text
from ooxml_edit import provenance
from ooxml_edit.tools import Dataset, DocumentFormat, Result, ScaleValue, ToolError, Toolbox, shared
from ooxml_edit.tools import string, tool
from ooxml_edit.tools import scales
from ooxml_edit.tools.scales import (column_target, parse, read_value, reference_matches,
                                     reference_target)

CLOCK = lambda: dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.timezone.utc)  # noqa: E731
NL_BE = dataclasses.replace(Locale(), decimal_separator=",", thousands_separator=".",
                            date_order="dmy", language="nl-BE", list_separator=";")
EN = Locale()

SCALE = {"name": "Score",
         "steps": [{"min": 1, "fill": "C62828", "label": "Low"},
                   {"min": 2.5, "fill": "F9A825"},
                   {"min": 4, "fill": "2E7D32", "label": "High"}],
         "targets": ["a1", "a2"]}


def scale(**changes):
    return {**SCALE, **changes}


# -- reading a scale -----------------------------------------------------------------------------


def test_white_or_black_text_is_the_one_of_more_contrast():
    assert ratio("FFFFFF", "000000") == pytest.approx(21.0)
    assert luminance("#FFFFFF") == pytest.approx(1.0)
    assert readable_text("2E7D32") == ("FFFFFF", 5.13)
    assert readable_text("F9A825") == ("000000", 10.66)
    assert readable_text("767676") == ("000000", 4.62)        # white has 4.54
    found = parse(SCALE)
    assert [(s.text, s.rule) for s in found.steps] == [("FFFFFF", "auto"), ("000000", "auto"),
                                                       ("FFFFFF", "auto")]
    assert found.notes == ()
    forced = parse(scale(steps=[{"min": 1, "fill": "F9A825", "text": "white"}]))
    assert forced.steps[0].text == "FFFFFF" and forced.steps[0].contrast < 4.5
    assert "below WCAG's 4.5:1" in forced.notes[0] and "text auto" in forced.notes[0]


def test_steps_take_ranges_from_their_min_to_the_next():
    found = parse(SCALE)
    assert found.by == "range" and found.name == "Score"
    assert [found.due(t, EN)[0].n for t in ("1", "2.49", "2.5", "3.99", "4", "17")] == [1, 1, 2, 2, 3, 3]
    step, why = found.due("0.5", EN)
    assert step is None and why == "0.5 is below the first step's min 1"
    assert found.style_name(found.steps[0]) == "Score 1 Low"
    assert found.style_name(found.steps[1]) == "Score 2"
    assert found.step_of_style("Score 3 High") == 2
    assert found.step_of_style("score 3 Excellent") == 2        # a label changed since
    assert found.step_of_style("Score 4") is None and found.step_of_style("Scores 1") is None


def test_a_max_makes_a_gap_that_is_meant():
    found = parse(scale(steps=[{"min": 1, "max": 2, "fill": "C62828"},
                               {"min": 3, "fill": "2E7D32"}]))
    assert found.notes == ("values above 2 and below 3 are in no step (step 1's max): skipped",)
    step, why = found.due("2.5", EN)
    assert step is None and why == "2.5 is in no step: between step 1's max 2 and step 2's min 3"


def test_steps_out_of_order_or_overlapping_are_refused_with_the_way_out():
    with pytest.raises(ToolError) as refused:
        parse(scale(steps=[{"min": 3, "fill": "2E7D32"}, {"min": 1, "fill": "C62828"}]))
    assert "lowest first" in refused.value.message and "[1.0, 3.0]" in refused.value.message
    with pytest.raises(ToolError) as refused:
        parse(scale(steps=[{"min": 1, "max": 3, "fill": "C62828"}, {"min": 2, "fill": "2E7D32"}]))
    assert "overlap" in refused.value.message
    with pytest.raises(ToolError) as refused:
        parse(scale(steps=[{"min": 1, "fill": "C62828"}, {"value": "5", "fill": "2E7D32"}]))
    assert "mix min and value" in refused.value.message
    with pytest.raises(ToolError) as refused:
        parse(scale(steps=[{"fill": "C62828"}]))
    assert "says no values" in refused.value.message
    with pytest.raises(ToolError) as refused:
        parse(scale(steps=[{"min": 1, "fill": "green"}]))
    assert refused.value.message.startswith("steps[0].fill 'green' is not a colour")
    assert refused.value.field == "scale" and refused.value.details["example"]["steps"]
    with pytest.raises(ToolError):
        parse(scale(targets=[]))
    with pytest.raises(ToolError):
        parse(scale(name="Level 2"))                 # its styles' numbers would run on


def test_exact_values_and_texts():
    found = parse(scale(steps=[{"value": n, "fill": f} for n, f in
                               ((1, "C62828"), (2, "EF6C00"), (3, "F9A825"), (4, "7CB342"),
                                (5, "2E7D32"))]))
    assert found.by == "value"
    assert found.due("3", EN)[0].n == 3 and found.due("3,0", NL_BE)[0].n == 3
    assert found.due("3.5", EN) == (None, "3.5 is no step's value; steps take 1, 2, 3, 4, 5")
    with pytest.raises(ToolError) as refused:
        parse(scale(steps=[{"value": 1, "fill": "C62828"}, {"value": 2, "fill": "F9A825"},
                           {"value": 4, "fill": "2E7D32"}]))
    assert "no step takes 3" in refused.value.message and "min/max" in refused.value.message
    texts = parse(scale(steps=[{"value": "Low", "fill": "C62828"}, {"value": "High", "fill": "2E7D32"}]))
    assert texts.by == "text" and texts.due("  high ", EN)[0].n == 2
    assert texts.due("Medium", EN) == (None, "'Medium' is no step's text; steps take 'Low', 'High'")
    with pytest.raises(ToolError):
        parse(scale(steps=[{"value": "Low", "fill": "C62828"}, {"value": "low", "fill": "2E7D32"}]))


def test_a_value_is_one_number_in_the_locale():
    assert read_value("2,5", NL_BE) == 2.5 and read_value("2.5", EN) == 2.5
    assert read_value("1.234,5", NL_BE) == 1234.5 and read_value("1,234.5", EN) == 1234.5
    assert read_value("85 %", EN) == 85 and read_value("3/5", EN) == 3
    assert read_value("3 out of 5", EN) == 3 and read_value("−2", EN) == -2
    assert read_value("n/a", EN) is None and read_value("2 of 3", EN) is None
    assert read_value("Score 3", EN) is None and read_value("", EN) is None


def test_targets_name_columns_and_references():
    assert column_target("t:3A1F09C2/col2") == ("t:3A1F09C2", 2)
    assert column_target("256.7/col0") == ("256.7", 0) and column_target("256.7/cell0,1") is None
    assert reference_target("{{d:scores/*/score}}") == "d:scores/*/score"
    assert reference_target("d:scores") is None
    assert reference_matches("d:scores/*/score", "d:scores/North/score")
    assert reference_matches("d:scores/*/score", "d:Scores/North/score:0.0")
    assert not reference_matches("d:scores/*/score", "d:scores/North/rate")
    assert reference_matches("*", "t:costs/#3/total")


# -- the toy board ---------------------------------------------------------------------------------


class Board:
    """Values ``{address: [text, step now or None, fill]}``; ``written``: the addresses whose
    text was written by reference, with their reference."""

    def __init__(self, values, written=None, labels=None):
        self.values = values
        self.written = written or {}
        self.labels = labels or {}           # address: the label record kept there


def board_values(call, found_scale, scope):
    board = call.document
    out = []
    for target in found_scale.targets:
        column = column_target(target)
        pattern = reference_target(target)
        if column is not None:
            names = [a for a in board.values if a.startswith(f"{column[0]}.") and
                     a.endswith(str(column[1]))]
            if not names:
                out.append(ScaleValue(target, skip=f"no column {target}"))
        elif pattern is not None:
            names = [a for a, source in board.written.items() if reference_matches(pattern, source)]
            names += [a for a, kept in board.labels.items() if a not in names
                      and reference_matches(pattern, kept.get("source") or "")]
        elif target in board.values:
            names = [target]
        else:
            out.append(ScaleValue(target, skip=f"no value {target}: values are {sorted(board.values)}"))
            continue
        for name in names:
            if scope.get("slides") and name[0] not in scope["slides"]:
                continue
            text, now, fill = board.values[name]
            exact = now is None or fill == found_scale.steps[now].fill
            out.append(ScaleValue(name, text, given=target, now=now, exact=exact,
                                  differs=None if exact else f"fill {fill}",
                                  where={"slide": name[0]}, kept=board.labels.get(name),
                                  source=board.written.get(name)))
    return out


WRITES: list[list[tuple[str, int]]] = []


def board_write(call, found_scale, plan, preview):
    if not preview:
        for value, step in plan:
            call.document.values[value.target][1:] = [step.index, step.fill]
            if found_scale.labels:
                call.document.values[value.target][0] = step.label
                call.document.labels[value.target] = {**value.record, "p": 0, "at": 0,
                                                      "text": step.label}
                call.document.written.pop(value.target, None)
        WRITES.append([(value.target, step.n) for value, step in plan])
    return Result(changed=[value.target for value, _ in plan], data={"styles": "direct"})


@shared.handler("style_by_value", kind="pptx")
def board_style(call, doc, scale, preview=False):
    return scales.apply(call, scale, bool(preview), board_write)


@shared.handler("check", kind="pptx")
def board_check(call, doc, slides=None, pages=None, include=None, boxes=False, spacing=None,
                contrast=None):
    return Result(summary="0 fit fact(s)", data={"overflows": []})


BOARD = DocumentFormat(kind="pptx", open=lambda data: Board({}), detect=lambda data, name: False,
                       scale_values=board_values)
BARE = DocumentFormat(kind="pptx", open=lambda data: Board({}), detect=lambda data, name: False)


@pytest.fixture(autouse=True)
def _clear():
    WRITES.clear()


@tool("keep_rows", "Keep a made-up dataset.", {"rate": string("Its North score.")}, documents=())
def keep_rows(call, rate):
    rows = [("North", float(rate)), ("South", 3.0)]
    call.define_dataset("scores", Dataset(("topic", "score"), rows, keys=1), source=None)
    return Result(summary="Kept d:scores")


def _box(fmt=BOARD):
    return Toolbox([board_style, board_check, keep_rows, *shared.SESSION_TOOLS], formats=[fmt],
                   groups=shared.GROUPS, workers=0)


def _session(box, values, written=None, labels=None):
    session = box.session(clock=CLOCK)
    session.adopt(Board(values, written, labels), "pptx", "board.pptx")
    return session


def _values():
    return {"a1": ["1.5", None, None], "a2": ["4,5", None, None], "a3": ["n/a", None, None],
            "b1": ["3", 0, "C62828"], "c.1": ["2", None, None], "c.2": ["4.2", 2, "2E7D32"]}


def test_style_by_value_styles_each_value_with_its_step_and_says_so():
    with _box() as box:
        session = _session(box, _values())
        result = box.dispatch(session, "style_by_value", {
            "doc": "d1", "scale": scale(targets=["a1", "a2", "a3", "b1", "zz", "c/col2"])})
        assert result.ok, result.to_json()
        data = result.data
        assert data["targets"] == [
            {"target": "a1", "value": "1.5", "step": 1},
            {"target": "a2", "value": "4,5", "step": 3},          # the other convention's decimal
            {"target": "a3", "value": "n/a", "skipped": "'n/a' is not one number"},
            {"target": "b1", "value": "3", "step": 2, "was": 1},
            {"target": "zz", "skipped": "no value zz: values are ['a1', 'a2', 'a3', 'b1', 'c.1', 'c.2']"},
            {"target": "c.2", "from": "c/col2", "value": "4.2", "step": 3, "unchanged": True}]
        assert data["steps"][0] == {"step": 1, "label": "Low", "fill": "C62828", "text": "FFFFFF",
                                    "contrast": 5.62}
        assert data["styles"] == "direct"
        assert result.summary == "Score: styled 3 value(s), 1 already in their step, 2 skipped"
        assert WRITES == [[("a1", 1), ("a2", 3), ("b1", 2)]]
        assert session.documents["d1"].document.values["b1"] == ["3", 1, "F9A825"]


def test_preview_changes_nothing_and_a_value_in_its_step_is_no_error():
    with _box() as box:
        session = _session(box, _values())
        preview = box.dispatch(session, "style_by_value", {"doc": "d1", "scale": SCALE, "preview": True})
        assert preview.ok and preview.summary.startswith("Score: would style 2 value(s)")
        assert WRITES == [] and session.documents["d1"].document.values["a1"][1] is None
        assert box.dispatch(session, "style_by_value", {"doc": "d1", "scale": SCALE}).ok
        again = box.dispatch(session, "style_by_value", {"doc": "d1", "scale": SCALE})
        assert again.ok and again.summary == "Score: styled 0 value(s), 2 already in their step, 0 skipped"
        assert "nothing to change" in again.warnings[0]


def test_a_refused_scale_names_the_way_out():
    with _box() as box:
        session = _session(box, _values())
        refused = box.dispatch(session, "style_by_value", {
            "doc": "d1", "scale": scale(steps=[{"min": 4, "fill": "2E7D32"}, {"min": 1, "fill": "C62828"}])})
        assert not refused.ok and refused.error.code == "invalid_arguments"
        assert "lowest first" in refused.error.message


def test_a_format_without_the_hook_is_refused():
    with _box(BARE) as box:
        session = _session(box, _values())
        refused = box.dispatch(session, "check", {"doc": "d1", "scale": SCALE})
        assert refused.error.code == "invalid_arguments" and refused.error.field == "scale"


def test_check_lists_values_with_the_wrong_step_or_none_and_the_one_fix():
    values = _values()
    values["a1"] = ["4.5", 0, "C62828"]           # styled Low, the value changed since
    values["c.1"] = ["3", 1, "FFC000"]            # the right step, its fill changed
    with _box() as box:
        session = _session(box, values, written={"a2": "d:scores/North/score",
                                                 "b1": "d:scores/South/score",
                                                 "c.2": "d:scores/West/rate"})
        result = box.dispatch(session, "check", {
            "doc": "d1", "scale": scale(targets=["a1", "{{d:scores/*/score}}", "c/col1", "a3"])})
        assert result.ok, result.to_json()
        found = result.data["scale"]
        assert found["wrong"] == [
            {"target": "a1", "slide": "a", "value": "4.5", "step": 1, "due": 3},
            {"target": "b1", "from": "{{d:scores/*/score}}", "slide": "b", "value": "3", "step": 1,
             "due": 2},
            {"target": "c.1", "from": "c/col1", "slide": "c", "value": "3", "step": 2, "due": 2,
             "differs": "fill FFC000"}]
        assert found["none"] == [{"target": "a2", "from": "{{d:scores/*/score}}", "slide": "a",
                                  "value": "4,5", "due": 3}]
        assert found["skipped"] == [{"target": "a3", "slide": "a", "value": "n/a",
                                     "why": "'n/a' is not one number"}]
        assert found["checked"] == 4 and found["right"] == 0
        assert found["fix"] == {"tool": "style_by_value", "arguments": {
            "doc": "d1", "scale": {**SCALE, "targets": ["a1", "b1", "c.1", "a2"]}}}
        assert result.summary == "scale: 0 of 4 value(s) in their step, 4 not, 1 skipped"
        assert set(result.data) == {"scale"}          # the scale alone asks only for its fact
        fixed = box.dispatch(session, "style_by_value", found["fix"]["arguments"])
        assert fixed.ok
        again = box.dispatch(session, "check", {
            "doc": "d1", "scale": scale(targets=["a1", "{{d:scores/*/score}}", "c/col1"])})
        assert again.data["scale"]["right"] == 4 and "fix" not in again.data["scale"]


def test_check_with_scale_and_other_facts_and_its_scope():
    with _box() as box:
        session = _session(box, _values())
        both = box.dispatch(session, "check", {"doc": "d1", "include": ["fit"], "scale": SCALE})
        assert both.data["overflows"] == [] and both.data["scale"]["none"]
        assert both.summary.startswith("0 fit fact(s); scale:")
        scoped = box.dispatch(session, "check", {"doc": "d1", "slides": ["b"],
                                                 "scale": scale(targets=["a1", "b1"])})
        assert [e["target"] for e in scoped.data["scale"]["wrong"]] == ["b1"]
        assert scoped.data["scale"]["checked"] == 1


def test_check_caps_what_it_lists():
    values = {f"a{k}": ["1", None, None] for k in range(30)}
    with _box() as box:
        session = _session(box, values)
        result = box.dispatch(session, "check", {"doc": "d1", "scale": scale(targets=sorted(values))})
        found = result.data["scale"]
        assert len(found["none"]) == scales.CHECK_LISTED and found["left_out"] == 10
        assert len(found["fix"]["arguments"]["scale"]["targets"]) == 30


def test_the_definitions():
    tool = shared.definition("style_by_value")
    assert tool.group == shared.MISC and tool.mutates
    assert set(tool.canonical["properties"]) == {"doc", "scale", "preview"}
    assert tool.canonical["properties"]["scale"]["properties"]["show"]["enum"] == ["value", "label"]
    assert shared.definition("check").canonical["properties"]["scale"]["properties"]["show"]
    steps = tool.canonical["properties"]["scale"]["properties"]["steps"]["items"]
    assert set(steps["properties"]) == {"fill", "min", "max", "value", "text", "label"}
    check = shared.definition("check").canonical["properties"]["scale"]
    assert "description" not in check["properties"]["steps"]["items"]["properties"]["fill"]
    with _box() as box:
        assert box.tools["style_by_value"].reads({"preview": True})
        assert not box.tools["style_by_value"].reads({})
        labelled = {"scale": scale(show="label")}
        assert box.tools["style_by_value"].value_reads(labelled)
        assert box.tools["check"].value_reads(labelled)
        assert not box.tools["check"].value_reads({"scale": SCALE})


# -- labels (round 38) ---------------------------------------------------------------------------

LABELLED = [{"min": 1, "fill": "C62828", "label": "Low"},
            {"min": 2.5, "fill": "F9A825", "label": "Middle"},
            {"min": 4, "fill": "2E7D32", "label": "High"}]


def labels(**changes):
    return scale(**{"steps": LABELLED, "show": "label", **changes})


def test_a_label_follows_edits_of_its_own_text():
    def one(text, at=0, p=0, **more):
        return {"p": p, "at": at, "text": text, "value": 1.4, **more}

    whole = [one("Low")]
    assert provenance.labels_after_edit(whole, "Low", "Lowish") == [one("Lowish")]
    assert provenance.labels_after_edit(whole, "Low", "2,7") == [one("2,7")]
    assert provenance.labels_after_edit(whole, "Low", "") == []
    inner = [one("Low", at=7)]
    old = "Level: Low here"
    assert provenance.labels_after_edit(inner, old, "Level: Lower here") == [one("Lower", at=7)]
    assert provenance.labels_after_edit(inner, old, "Our level: Low here") == [one("Low", at=11)]
    assert provenance.labels_after_edit(inner, old, "Level: Low there") == [one("Low", at=7)]
    assert provenance.labels_after_edit(inner, old, "Level: Lo") == []         # past its edge
    assert provenance.labels_after_edit(inner, old, "Level: Hi here") == [one("Hi", at=7)]
    assert provenance.labels_after_edit(inner, old, old) == inner
    # A fact, by contrast, goes with an edit of its text; read without the text it was
    # kept with, a whole holder's label is its text now.
    assert provenance.facts_after_edit(inner, old, "Level: Lower here") == []
    assert provenance.labels_found(whole, "Medium", whole=True) == [one("Medium")]
    assert provenance.labels_found(whole, "Medium") == []


def test_show_label_needs_a_label_per_step_none_twice():
    assert parse(labels()).labels and parse(labels(show="labels")).show == "label"
    assert not parse(SCALE).labels and parse(scale(show="")).show == "value"
    with pytest.raises(ToolError) as refused:
        parse(scale(show="label"))                      # step 2 has no label
    assert "step 2 has none: give every step a label" in refused.value.message
    with pytest.raises(ToolError) as refused:
        parse(labels(steps=[{**LABELLED[0]}, {**LABELLED[1], "label": "low"}]))
    assert "steps 1 and 2 are both labelled 'low'" in refused.value.message
    with pytest.raises(ToolError) as refused:
        parse(scale(show="name"))
    assert "value (the text is the value, the default) or label" in refused.value.message
    found = parse(labels())
    assert found.step_of_label(" high ").n == 3 and found.step_of_label("2") is None


def test_style_by_value_writes_each_label_and_keeps_its_value():
    with _box() as box:
        session = _session(box, _values(), written={"a2": "d:scores/North/score"})
        board = session.documents["d1"].document
        preview = box.dispatch(session, "style_by_value", {"doc": "d1", "scale": labels(), "preview": True})
        assert preview.ok and preview.summary == "Score: would label 2 value(s), 0 already in their step, 0 skipped"
        assert board.values["a1"][0] == "1.5" and board.labels == {}
        result = box.dispatch(session, "style_by_value", {"doc": "d1", "scale": labels()})
        assert result.ok, result.to_json()
        assert result.data["targets"] == [{"target": "a1", "value": "1.5", "step": 1, "label": "Low"},
                                          {"target": "a2", "value": "4,5", "step": 3, "label": "High"}]
        assert board.values["a1"] == ["Low", 0, "C62828"]
        assert board.labels["a1"] == {"label": "Low", "value": 1.5, "scale": "Score", "step": 1,
                                      "p": 0, "at": 0, "text": "Low"}
        assert board.labels["a2"]["source"] == "d:scores/North/score" and board.labels["a2"]["value"] == 4.5
        again = box.dispatch(session, "style_by_value", {"doc": "d1", "scale": labels()})
        assert again.data["targets"][0] == {"target": "a1", "value": "1.5", "shows": "Low", "step": 1,
                                            "label": "Low", "unchanged": True}
        # The reference still names the value now its label is there.
        found = box.dispatch(session, "check", {"doc": "d1", "scale": labels(
            targets=["{{d:scores/*/score}}"])}).data["scale"]
        assert found["labels"] == [] and found["right"] == 1 and found["checked"] == 1
        assert "could not be read now" in found["notes"][0]      # no dataset d:scores here


def test_check_flags_labels_that_are_not_their_values():
    kept = {"a1": {"label": "Low", "value": 1.5, "scale": "Score", "step": 1, "p": 0, "at": 0, "text": "Lowish"},
            "b1": {"label": "Low", "value": 1.4, "source": "d:scores/North/score", "scale": "Score",
                   "step": 1, "p": 0, "at": 0, "text": "Low"},
            "c.1": {"label": "Middle", "value": 3, "scale": "Score", "step": 2, "p": 0, "at": 0,
                    "text": "Middle"}}
    values = {"a1": ["Lowish", 0, "C62828"], "a2": ["High", None, None], "a3": ["4,5", None, None],
              "b1": ["Low", 0, "C62828"], "c.1": ["Middle", 1, "F9A825"]}
    with _box() as box:
        session = _session(box, values, labels=kept)
        assert box.dispatch(session, "keep_rows", {"rate": "2.7"}).ok      # North was 1.4
        given = labels(targets=["a1", "a2", "a3", "b1", "c.1"])
        result = box.dispatch(session, "check", {"doc": "d1", "scale": given})
        assert result.ok, result.to_json()
        found = result.data["scale"]
        assert found["labels"] == [
            {"target": "a1", "slide": "a", "value": "1.5", "shows": "Lowish", "label": "Low", "due": 1,
             "why": "edited: written 'Low'"},
            {"target": "a3", "slide": "a", "value": "4,5", "shows": "4,5", "label": "High", "due": 3,
             "why": "shows its value, not its label"},
            {"target": "b1", "slide": "b", "value": "2.7", "shows": "Low", "label": "Middle", "due": 2,
             "why": "its value changed: recorded 1.4, now 2.7"}]
        assert found["wrong"] == [{"target": "b1", "slide": "b", "value": "2.7", "shows": "Low", "step": 1,
                                   "due": 2}]
        assert found["unrecorded"] == [{"target": "a2", "slide": "a", "shows": "High", "label_of": 3}]
        assert "unrecorded: a step's label with no value kept" in found["notes"][0]
        assert (found["checked"], found["right"]) == (5, 1)
        assert found["fix"]["arguments"]["scale"]["targets"] == ["b1", "a3", "a1"]
        assert result.summary == "scale: 1 of 5 value(s) in their step, 4 not"
        fixed = box.dispatch(session, "style_by_value", found["fix"]["arguments"])
        assert fixed.ok, fixed.to_json()
        board = session.documents["d1"].document
        assert board.values["b1"] == ["Middle", 1, "F9A825"] and board.labels["b1"]["value"] == 2.7
        assert board.values["a1"][0] == "Low" and board.values["a3"][0] == "High"
        again = box.dispatch(session, "check", {"doc": "d1", "scale": given}).data["scale"]
        assert (again["right"], again["labels"], again["wrong"]) == (4, [], [])
        assert [e["target"] for e in again["unrecorded"]] == ["a2"] and "fix" not in again
        skipped = box.dispatch(session, "style_by_value", {"doc": "d1", "scale": given})
        assert skipped.data["targets"][1]["skipped"].startswith(
            "'High' is step 3's label, and no value is recorded for it: write its value there")


def test_a_label_read_as_a_value_scale_says_how_and_text_steps_still_work():
    kept = {"a1": {"label": "Low", "value": 1.5, "scale": "Score", "step": 1, "p": 0, "at": 0, "text": "Low"}}
    with _box() as box:
        session = _session(box, {"a1": ["Low", 0, "C62828"]}, labels=kept)
        found = box.dispatch(session, "check", {"doc": "d1", "scale": scale(targets=["a1"])}).data["scale"]
        assert found["skipped"][0]["why"] == ("'Low' is not one number; it is step 1's label: give the "
                                              "scale show label")
        texts = scale(steps=[{"value": "Low", "fill": "C62828"}, {"value": "High", "fill": "2E7D32"}],
                      targets=["a1"])
        assert box.dispatch(session, "check", {"doc": "d1", "scale": texts}).data["scale"]["right"] == 1


def test_several_labels_with_no_value_kept_are_unrecorded_too():
    # A cell merged with another holds both labels, one per line, and neither record.
    with _box() as box:
        session = _session(box, {"a1": ["High\nLow", None, None], "a2": ["High\n2", None, None]})
        found = box.dispatch(session, "check", {"doc": "d1", "scale": labels(targets=["a1", "a2"])}).data["scale"]
        assert found["unrecorded"] == [{"target": "a1", "slide": "a", "shows": "High\nLow", "label_of": [3, 1]}]
        assert found["skipped"][0]["why"] == "'High 2' is not one number, nor a step's label"
        result = box.dispatch(session, "style_by_value", {"doc": "d1", "scale": labels(targets=["a1"])})
        assert result.data["targets"][0]["skipped"].startswith(
            "'High Low' holds the labels of steps 3, 1, one per line, and no value is recorded for it: "
            "write its one value there")
