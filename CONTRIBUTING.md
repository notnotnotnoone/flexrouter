# Contributing

## Setup

```bash
git clone https://github.com/notnotnotnoone/flexrouter.git
cd flexrouter
pip install -e ".[dev]"
```

Requires Python 3.11+.

## Running tests

```bash
pytest
```

CI runs the same suite on Python 3.11 and 3.12 for every push and pull request.

The dashboard also has a small set of real-browser tests that a plain `pytest` skips. If you change anything in `flexrouter/dashboard/static/app.js` or the markup it hooks into, run them too:

```bash
uv run playwright install chromium   # once
uv run pytest -m browser
```

## Submitting changes

1. Fork the repo and create a branch off `master`.
2. Make your change, with tests for any new behavior.
3. Make sure `pytest` passes locally.
4. Open a pull request describing what changed and why.

## Releasing (maintainers)

Tag a release to publish to PyPI:

```bash
git tag v0.1.0
git push origin master --tags
```

Requires a [PyPI trusted publisher](https://docs.pypi.org/trusted-publishers/) configured for this repo with environment `pypi`.
