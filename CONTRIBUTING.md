# Contributing

## Running the tests

```sh
pip install -e '.[dev]'
python -m pytest -q
```

CI runs the suite on Linux, macOS and Windows with Python 3.10 to 3.13. The online checks
(`-m provider`) call a model provider's API and skip without its key; they never run on CI.

## What does not go in the repository

- No Microsoft fonts, and no output of Microsoft Office (PDFs, rendered images, saved
  documents used as references).
- No third-party documents. Tests build the packages they need in code
  (`tests/synthetic.py`, `tests/charts_synthetic.py`).
- No format vocabulary in the core: `tests/test_neutrality.py` must keep passing.

## Pull requests

Open pull requests against `main`. Keep a change to one subject, add tests for it, and
note user-visible changes in [CHANGELOG.md](CHANGELOG.md).
