"""Tests for parser-backed HTML assertion helpers."""

import pytest

from tests.support.html import (
    assert_form,
    assert_has_element,
    assert_input,
    assert_link,
    assert_no_element,
    assert_not_visible_text,
    assert_option,
    assert_visible_text,
    parse_html,
)


HTML = """
<html>
  <head>
    <style>.hidden { content: "not visible"; }</style>
    <script>window.label = "script only";</script>
  </head>
  <body>
    <a class="btn btn-primary" href="/rules/audit">Rule audit</a>
    <form action="/rules/audit/preview" method="post">
      <input name="action" value="apply_all_rules">
      <input name="confirm_preview" value="1" checked>
      <button>Preview apply all</button>
    </form>
    <select name="approval">
      <option value="suggested" selected>Suggested</option>
    </select>
  </body>
</html>
"""


def test_visible_text_helpers_ignore_script_and_style_content():
    """Verify visible text assertions are based on parsed document text."""
    assert_visible_text(HTML, "Rule audit", "Preview apply all")
    assert_not_visible_text(HTML, "script only", "not visible")


def test_element_helpers_match_semantic_attributes_and_text():
    """Verify semantic helpers can find parsed links, forms, inputs, and options."""
    document = parse_html(HTML)

    assert document.has_element("a", attrs={"class": "btn-primary"}, text="Rule audit")
    assert_link(HTML, "/rules/audit", text="Rule audit")
    assert_form(HTML, "/rules/audit/preview", method="post", text="Preview apply all")
    assert_input(HTML, name="action", value="apply_all_rules")
    assert_input(HTML, name="confirm_preview", value="1", checked=True)
    assert_option(HTML, value="suggested", text="Suggested", selected=True)
    assert_no_element(HTML, "a", attrs={"href": "/missing"})


def test_element_helpers_raise_on_missing_markup():
    """Verify missing semantic markup fails with an assertion."""
    with pytest.raises(AssertionError):
        assert_has_element(HTML, "button", attrs={"type": "submit"})
