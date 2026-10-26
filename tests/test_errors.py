"""The typed refusals (ooxml_edit.errors) and the codes the dispatcher gives them (review, S9).

``unit`` says a value is out of its range or in the wrong unit; before these classes every
plain ``ValueError`` a library raised was ``unit``, whatever it refused -- the last node of
a diagram among them.  Now a refusal of a value is ``invalid_arguments``, in every format.
"""

from __future__ import annotations

import datetime as dt

import pytest

from ooxml_edit import dates
from ooxml_edit.errors import ArgumentError, ArgumentTypeError, UnitError
from ooxml_edit.sheets.workbook import Cell, column_letters, date_serial, number_text
from ooxml_edit.tools import DocumentFormat, LimitError, Tool, Toolbox, string, tool
from ooxml_edit.tools.dispatch import BASE_ERRORS

import synthetic
import tools_toys


def test_the_family():
    assert issubclass(ArgumentError, ValueError)
    assert issubclass(ArgumentTypeError, ArgumentError) and issubclass(ArgumentTypeError,
                                                                        TypeError)
    assert issubclass(UnitError, ArgumentError)
    assert BASE_ERRORS == {ArgumentError: "invalid_arguments", UnitError: "unit"}


class _FormatOwn(ArgumentError):
    """A format library's own argument error, mapped by its format to ``refused``."""


class _Derived(ArgumentError):
    """A format library's own argument error, unmapped: its family's code."""


@pytest.fixture
def toolbox():
    with Toolbox(tools_toys.TOOLS, formats=tools_toys.FORMATS, groups=tools_toys.GROUPS,
                 error_map={_FormatOwn: "refused"}) as box:
        yield box


@pytest.mark.parametrize("error, code", [
    (ArgumentError("a diagram keeps at least one node"), "invalid_arguments"),
    (ArgumentTypeError("a cell holds text, not list"), "invalid_arguments"),
    (UnitError("1899-12-31 is before 1 January 1900"), "unit"),
    (_Derived("a format's own refusal"), "invalid_arguments"),
    (_FormatOwn("mapped by its format"), "refused"),
    # What callers relied on before: a plain ValueError (Python's own, or a library's
    # subclass none of these) is still ``unit``; a plain TypeError still ``internal``.
    (ValueError("invalid literal for int()"), "unit"),
    (TypeError("unsupported operand"), "internal"),
    (LimitError("too big"), "limit"),
    (KeyError("no such part"), "not_found"),
])
def test_the_codes(toolbox, error, code):
    assert toolbox.map_exception(error).code == code


def _raises(error: BaseException) -> Tool:
    @tool("toy_refuse", "Test only: raise an error.",
          {"doc": string("The document.")}, mutates=True)
    def toy_refuse(call, doc):
        raise error
    return toy_refuse


@pytest.mark.parametrize("error, code", [
    (ArgumentError("a diagram keeps at least one node"), "invalid_arguments"),
    (UnitError("text is longer than a cell holds"), "unit"),
])
def test_a_handler_s_refusal_reaches_the_model_with_its_code(error, code):
    with Toolbox([*tools_toys.TOOLS, _raises(error)], formats=tools_toys.FORMATS,
                 groups=tools_toys.GROUPS) as box:
        session = box.session()
        session.open(synthetic.outer_package(), "deck.pptx")
        result = box.dispatch(session, "toy_refuse", {"doc": "d1"})
    assert not result.ok and result.error.code == code
    assert result.error.message == str(error)
    assert session.entry("d1").version == 0


def test_a_format_maps_its_own_subclass():
    fmt = DocumentFormat(kind="pptx", open=tools_toys.FORMATS[0].open,
                         detect=tools_toys.FORMATS[0].detect, errors={_FormatOwn: "refused"})
    with Toolbox(tools_toys.TOOLS, formats=[fmt, *tools_toys.FORMATS[1:]],
                 groups=tools_toys.GROUPS) as box:
        assert box.map_exception(_FormatOwn("x")).code == "refused"
        assert box.map_exception(_Derived("x")).code == "invalid_arguments"


# -- what the libraries raise -------------------------------------------------------------


def test_a_cell_reference_is_an_argument_and_a_cell_outside_the_sheet_a_unit():
    with pytest.raises(ArgumentError, match="is not a cell reference") as refused:
        Cell.parse("A0B")
    assert not isinstance(refused.value, UnitError)
    with pytest.raises(UnitError, match="outside the sheet"):
        column_letters(0)
    with pytest.raises(UnitError, match="not a finite number"):
        number_text(float("nan"))
    with pytest.raises(UnitError, match="before 1 January 1900"):
        date_serial(dt.date(1899, 12, 1))
    with pytest.raises(ArgumentTypeError, match="is not a date"):
        date_serial("2026-01-01")  # type: ignore[arg-type]


def test_a_chart_date_before_its_range_is_a_unit_and_a_bad_category_an_argument():
    with pytest.raises(UnitError, match="before 1 March 1900"):
        dates.serial(dt.date(1900, 1, 1))
    with pytest.raises(ArgumentTypeError):
        dates.DateCategory("2026-01-01", "d mmm")  # type: ignore[arg-type]
    with pytest.raises(ArgumentError, match="format_code"):
        dates.DateCategory(dt.date(2026, 1, 1), " ")
