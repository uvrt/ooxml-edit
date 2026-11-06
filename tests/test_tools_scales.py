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
from ooxml_edit.tools import DocumentFormat, Result, ScaleValue, ToolError, Toolbox, shared
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

    def __init__(self, values, written=None):
        self.values = values
        self.written = written or {}


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
                                  where={"slide": name[0]}))
    return out


WRITES: list[list[tuple[str, int]]] = []


def board_write(call, found_scale, plan, preview):
    if not preview:
        for value, step in plan:
            call.document.values[value.target][1:] = [step.index, step.fill]
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


def _box(fmt=BOARD):
    return Toolbox([board_style, board_check, *shared.SESSION_TOOLS], formats=[fmt],
                   groups=shared.GROUPS, workers=0)


def _session(box, values, written=None):
    session = box.session(clock=CLOCK)
    session.adopt(Board(values, written), "pptx", "board.pptx")
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
    steps = tool.canonical["properties"]["scale"]["properties"]["steps"]["items"]
    assert set(steps["properties"]) == {"fill", "min", "max", "value", "text", "label"}
    check = shared.definition("check").canonical["properties"]["scale"]
    assert "description" not in check["properties"]["steps"]["items"]["properties"]["fill"]
    with _box() as box:
        assert box.tools["style_by_value"].reads({"preview": True})
        assert not box.tools["style_by_value"].reads({})
