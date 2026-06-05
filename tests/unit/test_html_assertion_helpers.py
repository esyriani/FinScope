"""Tests for parser-backed HTML assertion helpers."""

import pytest

from tests.support.html import (
    assert_asset_reference,
    assert_form,
    assert_has_element,
    assert_input,
    assert_link,
    assert_no_element,
    assert_no_asset_reference,
    assert_not_visible_text,
    assert_option,
    assert_visible_text,
    asset_reference_index,
    asset_reference_values,
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

ASSET_HTML = """
<html>
  <head>
    <link href="/static/css/app.css?v=abcdef123456" rel="stylesheet">
    <script src="/static/js/app.js?v=abcdef123456"></script>
  </head>
  <body>
    <img src="/static/img/logo.png">
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


def test_asset_reference_helpers_parse_sources_and_hrefs():
    """Verify asset helpers inspect parsed ``src`` and ``href`` values."""
    assert asset_reference_values(ASSET_HTML) == [
        "/static/css/app.css?v=abcdef123456",
        "/static/js/app.js?v=abcdef123456",
        "/static/img/logo.png",
    ]
    assert asset_reference_index(ASSET_HTML, r"/static/js/app\.js") == 1
    assert_asset_reference(ASSET_HTML, r"/static/css/app\.css\?v=[0-9a-f]{12}")
    assert_no_asset_reference(ASSET_HTML, "cdn.jsdelivr.net")
