"""Shared pytest config. Adds an opt-in `slow` marker so the heavy end-to-end
OCR regression test (renders PDFs at 300 dpi + ECC registration, ~minutes) does
not run on every `pytest` invocation. Run it explicitly with `--runslow`."""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--runslow", action="store_true", default=False,
        help="run slow end-to-end OCR accuracy regression tests",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: heavy end-to-end test, opt-in via --runslow")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        return
    skip_slow = pytest.mark.skip(reason="need --runslow to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
