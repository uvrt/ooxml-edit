# Contributing

## Running the tests

```sh
pip install "ooxml-common @ git+https://github.com/uvrt/ooxml-common@main"
pip install -e '.[dev]'
python -m pytest -q
```

ooxml-common is a dependency and is not on PyPI, so it goes in first (from its repository
or a checkout, `pip install -e ../ooxml-common`).

CI runs the suite on Linux, macOS and Windows with Python 3.10 to 3.15. The online checks
(`-m provider`) call a model provider's API and skip without its key; they never run on CI.
CI also runs pptx-agent's, docx-agent's and xlsx-agent's suites, at their `main`, against
the change (the `downstream` job), and the whole workflow nightly.

The `build` job also lints and type checks; run both before a pull request (the `dev`
extra pins them):

```sh
ruff check .
mypy
```

ruff runs bug-finding rules only (no formatting, no import sorting); mypy is non-strict.
A `# noqa` or `# type: ignore[code]` says why on its line.

## What does not go in the repository

- No Microsoft fonts, and no output of Microsoft Office (PDFs, rendered images, saved
  documents used as references).
- No third-party documents. Tests build the packages they need in code
  (`tests/synthetic.py`, `tests/charts_synthetic.py`).
- No format vocabulary in the core: `tests/test_neutrality.py` must keep passing.
- No copy of ooxml-common's logic. ooxml-edit may import ooxml-common; ooxml-common never
  imports ooxml-edit (its `tests/test_independence.py`). What both need read the same way
  -- a number format code, the measured date names, the locale, the package's path rule
  -- is ooxml-common's, and ooxml-edit re-exports it under its own names where siblings
  import them. Nothing else third-party but lxml (`tests/test_neutrality.py`).

## Pull requests

Open pull requests against `main`. Keep a change to one subject, add tests for it, and
note user-visible changes in [CHANGELOG.md](CHANGELOG.md).
Raise the version with its CHANGELOG section: CI's `build` job checks that
`pyproject.toml`'s version is the one its top section names, and `__version__` the same
(`.github/check_version.py`). Each version that reaches `main` is tagged `v<version>` on
that commit by the tag workflow (`.github/workflows/tag.yml`).

## Names the siblings import

`tests/test_public_api.py` imports every name a sibling imports from here, and
[docs/USED-BY.md](docs/USED-BY.md) lists the same names, so renaming or removing one fails
this repository's CI instead of a sibling's. To rename one, keep the old name as an alias
for one release (the core names no format, so the test's `OLD_NAMES` and `USED_BY` say
who it is kept for), and move the sibling over; a sibling that starts importing a name
adds it to both. Siblings import no name that starts
with an underscore: give it a public name first.
