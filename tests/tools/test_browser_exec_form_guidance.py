"""browser_exec's helper digest must keep date-like inputs away from keystroke typing.

Re-exam 2026-10-01: fill_input('#date', ...) on a Chinese-locale Chrome left the date input reading
"202609-02-08" instead of 2026-09-28, and the form was submitted anyway.
"""

from tools.browser_use_cli import _HELPERS_DIGEST


def test_date_like_inputs_are_set_by_value_instead_of_typed():
    assert "type=date" in _HELPERS_DIGEST
    assert "e.value = " in _HELPERS_DIGEST


def test_form_values_are_read_back_before_submitting():
    assert "before submitting" in _HELPERS_DIGEST
