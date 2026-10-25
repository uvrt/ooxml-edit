# Contributing

## Running the tests

```sh
pip install -e '.[dev]'
python -m pytest -q
```

CI runs the suite on Linux, macOS and Windows with Python 3.10 to 3.15. The online checks
(`-m provider`) call a model provider's API and skip without its key; they never run on CI.
CI also runs pptx-agent's, docx-agent's and xlsx-agent's suites, at their `main`, against
the change (the `downstream` job), and the whole workflow nightly.

## What does not go in the repository

- No Microsoft fonts, and no output of Microsoft Office (PDFs, rendered images, saved
  documents used as references).
- No third-party documents. Tests build the packages they need in code
  (`tests/synthetic.py`, `tests/charts_synthetic.py`).
- No format vocabulary in the core: `tests/test_neutrality.py` must keep passing.

## Pull requests

Open pull requests against `main`. Keep a change to one subject, add tests for it, and
note user-visible changes in [CHANGELOG.md](CHANGELOG.md).

## Names the siblings import

`tests/test_public_api.py` imports every name a sibling imports from here, and
[docs/USED-BY.md](docs/USED-BY.md) lists the same names, so renaming or removing one fails
this repository's CI instead of a sibling's. To rename one, keep the old name as an alias
for one release, with a comment saying who it is kept for, and move the sibling over; a
sibling that starts importing a name adds it to both. Siblings import no name that starts
with an underscore: give it a public name first.
