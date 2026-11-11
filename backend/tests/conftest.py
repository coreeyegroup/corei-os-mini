"""Shared test setup: sensitive routes need a recently re-entered password (require_reauth).
Most tests exercise other rules, so the step-up is treated as done unless a test removes
these overrides (test_controls.ReauthFlow checks the real behaviour)."""
import os

import pytest

os.environ.setdefault('COREI_ALERTS', 'off')
os.environ.setdefault('COREI_EGRESS_IP', '203.0.113.200')


@pytest.fixture(autouse=True)
def _step_up_done():
    from corei_os_mini.api import app
    from corei_os_mini.operator_auth import optional_reauth, require_reauth
    app.dependency_overrides[require_reauth] = lambda: None
    app.dependency_overrides[optional_reauth] = lambda: True
    yield
    app.dependency_overrides.pop(require_reauth, None)
    app.dependency_overrides.pop(optional_reauth, None)
