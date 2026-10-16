"""Date patterns (round 6, R9): ``d mmmm yyyy`` and the other tokens as text in each locale's
measured names, as a number format code a chart keeps, and dates as a new chart's
categories.  The names are those measured in Excel for Mac, 2026-10-16; every date and
figure here is made up.
"""

from __future__ import annotations

import datetime as dt
import io
import pickle
import zipfile

import pytest
from lxml import etree

import charts_synthetic as cs
from ooxml_edit import dates
from ooxml_edit.charts import ChartDataError
from ooxml_edit.charts.create import add_chart, chart_space, chart_workbook
from ooxml_edit.dates import DateCategory, PatternError
from test_chart import chart_order_violations
from xlsx import check_chart_against_workbook

C = "{%s}" % cs.C
MONDAY = dt.date(2026, 3, 2)
CHRISTMAS = dt.date(2026, 12, 25)          # a Friday


# -- names, per measured locale ------------------------------------------------------------------


@pytest.mark.parametrize("language, months, short, days, short_days", [
    ("en-US", ("January", "December"), ("Jan", "Dec"), ("Monday", "Sunday"), ("Mon", "Sun")),
    ("en-GB", ("January", "December"), ("Jan", "Dec"), ("Monday", "Sunday"), ("Mon", "Sun")),
    ("nl-NL", ("januari", "december"), ("jan", "dec"), ("maandag", "zondag"), ("ma", "zo")),
    ("nl-BE", ("januari", "december"), ("jan", "dec"), ("maandag", "zondag"), ("ma", "zo")),
    ("de-DE", ("Januar", "Dezember"), ("Jan", "Dez"), ("Montag", "Sonntag"), ("Mo", "So")),
    ("fr-FR", ("janvier", "décembre"), ("janv.", "déc."), ("lundi", "dimanche"),
     ("lun.", "dim.")),
    ("es-ES", ("enero", "diciembre"), ("ene.", "dic."), ("lunes", "domingo"), ("lu.", "do.")),
    ("it-IT", ("gennaio", "dicembre"), ("gen", "dic"), ("lunedì", "domenica"),
     ("lun", "dom")),
])
def test_each_measured_locale_has_twelve_months_and_seven_days(language, months, short, days,
                                                               short_days):
    names = dates.names_for(language)
    assert len(names.months) == len(names.short_months) == 12
    assert len(names.days) == len(names.short_days) == 7
    assert (names.months[0], names.months[-1]) == months
    assert (names.short_months[0], names.short_months[-1]) == short
    assert (names.days[0], names.days[-1]) == days
    assert (names.short_days[0], names.short_days[-1]) == short_days


@pytest.mark.parametrize("language, written", [
    ("en-US", "2 March 2026"), ("en-GB", "2 March 2026"), ("nl-NL", "2 maart 2026"),
    ("nl-BE", "2 maart 2026"), ("de-DE", "2 März 2026"), ("fr-FR", "2 mars 2026"),
    ("es-ES", "2 marzo 2026"), ("it-IT", "2 marzo 2026"),
])
def test_d_mmmm_yyyy_as_measured(language, written):
    assert dates.format_date(MONDAY, "d mmmm yyyy", language) == written


@pytest.mark.parametrize("language, written", [
    ("en-US", "Mon Mar M · Fri Dec D"), ("nl-BE", "ma mrt m · vr dec d"),
    ("de-DE", "Mo Mär M · Fr Dez D"), ("fr-FR", "lun. mars m · ven. déc. d"),
    ("es-ES", "lu. mar. m · vi. dic. d"), ("it-IT", "lun mar m · ven dic d"),
])
def test_abbreviations_and_first_letters(language, written):
    pattern = "ddd mmm mmmmm"
    assert " · ".join(dates.format_date(day, pattern, language)
                      for day in (MONDAY, CHRISTMAS)) == written


def test_the_numeric_tokens_and_literals():
    assert dates.format_date(MONDAY, "d/m/yy", None) == "2/3/26"
    assert dates.format_date(MONDAY, "dd.mm.yyyy", "de-DE") == "02.03.2026"
    assert dates.format_date(MONDAY, "yyyy-mm-dd", "ja-JP") == "2026-03-02"
    assert dates.format_date(MONDAY, 'd "de" mmmm "de" yyyy', "es-ES") == "2 de marzo de 2026"
    assert dates.format_date(MONDAY, r"d\h", None) == "2h"
    assert dates.format_date(MONDAY, "DDDD, D MMMM YYYY", "en-GB") == "Monday, 2 March 2026"
    assert dates.format_date(dt.datetime(2026, 3, 2, 23, 59), "dd/mm/yyyy") == "02/03/2026"


def test_a_locale_is_read_by_its_language_tag():
    class Settings:
        language = "nl-BE"

    assert dates.format_date(MONDAY, "mmmm", Settings()) == "maart"
    assert dates.format_date(MONDAY, "mmmm", "nl_be") == "maart"
    assert dates.format_date(MONDAY, "mmmm") == "March"                 # en-US, the default


@pytest.mark.parametrize("language", ["en-AU", "nl", "pt-BR", "ja-JP"])
def test_a_locale_without_measured_names_refuses_name_tokens_only(language):
    with pytest.raises(PatternError, match=f"no month or day names are measured for "
                                           f"{language}"):
        dates.format_date(MONDAY, "d mmm yyyy", language)
    assert dates.format_date(MONDAY, "dd/mm/yyyy", language) == "02/03/2026"


@pytest.mark.parametrize("pattern, message", [
    ("", "a date pattern is text"),
    ("hh:mm", "'h' is no date token"),
    ("ddddd", "'ddddd' is no date token"),
    ("mmmmmm yyyy", "'mmmmmm' is no date token"),
    ("d m y", "'y' is no date token"),
    ("yyy", "'yyy' is no date token"),
    ("--", "has no date token"),
    ('d "de mmmm', "is not closed"),
    ("d mmmm yyyy week", "'w' is no date token"),
])
def test_a_pattern_that_is_none_is_refused_listing_the_tokens(pattern, message):
    with pytest.raises(PatternError, match=message):
        dates.parse(pattern)


# -- the code a chart keeps ----------------------------------------------------------------------


@pytest.mark.parametrize("language, code", [
    ("en-US", r"[$-409]d\ mmmm\ yyyy"), ("en-GB", r"[$-809]d\ mmmm\ yyyy"),
    ("nl-NL", r"[$-413]d\ mmmm\ yyyy"), ("nl-BE", r"[$-813]d\ mmmm\ yyyy"),
    ("de-DE", r"[$-407]d\ mmmm\ yyyy"), ("fr-FR", r"[$-40C]d\ mmmm\ yyyy"),
    ("es-ES", r"[$-C0A]d\ mmmm\ yyyy"), ("it-IT", r"[$-410]d\ mmmm\ yyyy"),
])
def test_the_code_is_stored_as_excel_stores_it(language, code):
    assert dates.format_code("d mmmm yyyy", language) == code


def test_the_codes_escapes():
    assert dates.format_code("dd/mm/yyyy", "nl-NL") == "[$-413]dd/mm/yyyy"
    assert dates.format_code("d-m-yy", "de-DE") == "[$-407]d-m-yy"
    assert dates.format_code("dd.mm.yyyy", "de-DE") == "[$-407]dd.mm.yyyy"
    assert dates.format_code("mmmm d, yyyy", "en-US") == r"[$-409]mmmm\ d\,\ yyyy"
    assert dates.format_code('d "de" mmmm', "es-ES") == r'[$-C0A]d\ "de"\ mmmm'
    assert dates.format_code("DD/MM/YYYY", "en-GB") == "[$-809]dd/mm/yyyy"
    # No measured locale id: no prefix, and a name token is refused as in text.
    assert dates.format_code("dd/mm/yyyy", "en-AU") == "dd/mm/yyyy"
    with pytest.raises(PatternError, match="en-AU"):
        dates.format_code("mmmm", "en-AU")


def test_serials_are_the_1900_systems():
    assert dates.serial(MONDAY) == 46083
    assert dates.serial(dt.date(1900, 3, 1)) == 61
    assert dates.serial(dt.datetime(2026, 3, 2, 12, 0)) == 46083.5
    assert dates.from_serial(46083) == MONDAY
    assert dates.from_serial(46083.5) == dt.datetime(2026, 3, 2, 12, 0)
    assert dates.from_serial(60) is None and dates.from_serial(True) is None
    with pytest.raises(ValueError, match="before 1 March 1900"):
        dates.serial(dt.date(1900, 2, 28))


def test_a_date_category_is_a_date_that_pickles():
    day = DateCategory(MONDAY, "[$-413]d\\ mmmm\\ yyyy")
    assert day == MONDAY and day.format_code == "[$-413]d\\ mmmm\\ yyyy"
    again = pickle.loads(pickle.dumps(day))
    assert again == MONDAY and again.format_code == day.format_code
    with pytest.raises(ValueError):
        DateCategory(MONDAY, "")


# -- dates as a new chart's categories -----------------------------------------------------------

CODE = dates.format_code("d mmm yyyy", "nl-BE")
DAYS = [DateCategory(dt.date(2026, 3, day), CODE) for day in (2, 9, 16)]
UNITS = [{"name": "Units", "values": [3, 5, 4]}]


def test_date_categories_are_serials_in_the_code_on_a_text_axis():
    root = chart_space("column", DAYS, UNITS, seed="s")
    cat = root.find(f".//{C}ser/{C}cat/{C}numRef")
    assert cat.find(f"{C}numCache/{C}formatCode").text == r"[$-813]d\ mmm\ yyyy"
    assert [pt.find(f"{C}v").text for pt in cat.iter(f"{C}pt")] == ["46083", "46090",
                                                                     "46097"]
    axis = root.find(f".//{C}catAx")
    assert axis.find(f"{C}numFmt").attrib == {"formatCode": r"[$-813]d\ mmm\ yyyy",
                                              "sourceLinked": "1"}
    assert axis.find(f"{C}auto").get("val") == "0"
    plain = chart_space("column", ["a", "b", "c"], UNITS, seed="s")
    assert plain.find(f".//{C}catAx/{C}auto").get("val") == "1"
    assert plain.find(f".//{C}catAx/{C}numFmt").get("formatCode") == "General"


def test_the_workbooks_category_cells_hold_the_serials_in_the_code():
    from xlsx import Book

    book = chart_workbook("column", DAYS, UNITS, number_format="0.0")
    assert Book(book).sheet("Sheet1").range("A2", "A4") == [46083, 46090, 46097]
    archive = zipfile.ZipFile(io.BytesIO(book))
    styles = etree.fromstring(archive.read("xl/styles.xml"))
    codes = {n.get("numFmtId"): n.get("formatCode") for n in styles.iter("{%s}numFmt" % cs.X)}
    assert codes == {"164": "0.0", "165": r"[$-813]d\ mmm\ yyyy"}
    xfs = [xf.get("numFmtId") for xf in styles.find("{%s}cellXfs" % cs.X)]
    assert xfs == ["0", "164", "165"]
    cells = etree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    styled = {c.get("r"): c.get("s") for c in cells.iter("{%s}c" % cs.X)}
    assert (styled["A2"], styled["B2"]) == ("2", "1")
    alone = zipfile.ZipFile(io.BytesIO(chart_workbook("column", DAYS, UNITS)))
    cells = etree.fromstring(alone.read("xl/worksheets/sheet1.xml"))
    assert {c.get("r"): c.get("s") for c in cells.iter("{%s}c" % cs.X)}["A2"] == "1"


def test_a_chart_of_dates_reads_back_and_its_workbook_matches():
    document = cs.Opened(cs.package())
    with document.history.batch():
        made = add_chart(document.package, cs.MAIN, "line", DAYS, UNITS)
        root = document.package.tree(cs.MAIN)
        frame = etree.SubElement(root, "{%s}frame" % cs.NS, name="new")
        frame.append(made.graphic())
        document.package.mark_dirty(cs.MAIN)
    chart = document.chart("new")
    assert chart.categories == [46083, 46090, 46097]
    # Read back as dates, for a model to read: ISO, whatever the code shows.
    assert chart.category_format == r"[$-813]d\ mmm\ yyyy"
    assert chart.category_dates == ["2026-03-02", "2026-03-09", "2026-03-16"]
    assert chart.workbook_values()["categories"]["values"] == [46083, 46090, 46097]
    data = document.to_bytes()
    cs.assert_valid(data)
    reopened = cs.Opened(data)
    part, workbook = reopened.chart("new").part, reopened.chart("new").workbook_part
    assert check_chart_against_workbook(reopened.package.read(part),
                                        reopened.package.read(workbook)) > 0
    assert not chart_order_violations(reopened.package.read(part))


@pytest.mark.parametrize("kind, categories, message", [
    ("column", [DAYS[0], "Q2"], "all dates or none"),
    ("scatter", DAYS, "x values"),
    ("column", [DAYS[0], DateCategory(dt.date(2026, 3, 9), "dd/mm/yyyy")], "one format code"),
    ("column", [DateCategory(dt.date(1900, 1, 1), CODE)], "before 1 March 1900"),
])
def test_date_categories_that_cannot_be_charted_are_refused(kind, categories, message):
    with pytest.raises(ChartDataError, match=message):
        chart_space(kind, categories, [{"name": "a", "values": [1] * len(categories)}])


@pytest.mark.parametrize("code, is_date", [
    (r"[$-413]d\ mmmm\ yyyy", True), ("m/d/yyyy", True), ("dd/mm/yy;@", True), ("h:mm", True),
    ("[h]:mm", True), ("General", False), ("@", False), ("#,##0.0", False), ('"days" 0', False),
    (r"\d0", False), ("[Red]0.0", False), ("[$€-413] #,##0.0", False), ("", False), (None, False),
])
def test_a_date_code_is_told_from_a_number_code(code, is_date):
    assert dates.is_date_code(code) is is_date


def test_text_and_number_categories_are_no_dates():
    document = cs.Opened(cs.package())
    with document.history.batch():
        for name, categories in (("text", ["a", "b", "c"]), ("numbers", [1, 2, 3])):
            made = add_chart(document.package, cs.MAIN, "column", categories, UNITS)
            root = document.package.tree(cs.MAIN)
            frame = etree.SubElement(root, "{%s}frame" % cs.NS, name=name)
            frame.append(made.graphic())
        document.package.mark_dirty(cs.MAIN)
    assert document.chart("text").category_format is None
    assert document.chart("text").category_dates is None
    assert document.chart("numbers").category_format == "General"
    assert document.chart("numbers").category_dates is None
