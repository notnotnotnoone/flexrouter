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

The suite is safe to run in parallel (`pytest-xdist` is in the `dev` extra):

```bash
pytest -n auto
```

The router's own waits (retry backoff, a penalised model's cooldown) do not
really sleep under test: `tests/conftest.py` gives every test a virtual clock
that those sleeps advance instead. A test that needs real waiting can opt out
with `@pytest.mark.real_clock`. Likewise the startup catalogue refresh is
stubbed so tests never call provider APIs; opt back in with
`@pytest.mark.real_startup_refresh`.

CI runs the same suite on Python 3.11 and 3.12 for every push and pull request.

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
