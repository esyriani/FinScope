"""HTML response assertion helpers for route tests.

Provides small parser-backed helpers that distinguish visible copy checks from
structural markup checks. Script and style blocks are ignored for copy
assertions so embedded catalogs or assets do not create false positives.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

INVISIBLE_TEXT_TAGS = {"script", "style"}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class HTMLElement:
    """Parsed HTML element used by semantic route-test assertions."""

    tag: str
    attrs: dict[str, str | None]
    text_parts: list[str] = field(default_factory=list)

    @property
    def text(self):
        """Return normalized visible text contained by this element."""
        return normalize_space("".join(self.text_parts))


class ParsedHTML(HTMLParser):
    """Collect elements and visible text from an HTML document."""

    def __init__(self):
        """Initialize parser state."""
        super().__init__(convert_charrefs=True)
        self.elements = []
        self._stack = []
        self._visible_parts = []

    @property
    def visible_text(self):
        """Return normalized document text outside invisible blocks."""
        return normalize_space("".join(self._visible_parts))

    def handle_starttag(self, tag, attrs):
        """Record a start tag and push it when it can contain text."""
        normalized_tag = tag.lower()
        node = HTMLElement(normalized_tag, dict(attrs))
        self.elements.append(node)
        if normalized_tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        """Record a self-closing tag."""
        self.elements.append(HTMLElement(tag.lower(), dict(attrs)))

    def handle_endtag(self, tag):
        """Pop the matching open element when a close tag is encountered."""
        normalized_tag = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].tag == normalized_tag:
                del self._stack[index:]
                break

    def handle_data(self, data):
        """Record visible text for the document and open visible elements."""
        if not data or self._inside_invisible_text():
            return
        self._visible_parts.append(data)
        for node in self._stack:
            if node.tag not in INVISIBLE_TEXT_TAGS:
                node.text_parts.append(data)

    def find_all(self, tag=None, attrs=None, text=None):
        """Return parsed elements matching tag, attributes, and contained text."""
        return [element for element in self.elements if element_matches(element, tag=tag, attrs=attrs, text=text)]

    def has_element(self, tag=None, attrs=None, text=None):
        """Return whether a matching element exists."""
        return bool(self.find_all(tag=tag, attrs=attrs, text=text))

    def _inside_invisible_text(self):
        """Return whether the parser is currently inside script or style."""
        return any(node.tag in INVISIBLE_TEXT_TAGS for node in self._stack)


def normalize_space(value):
    """Collapse HTML text whitespace to simplify semantic assertions."""
    return " ".join(str(value or "").split())


def response_html(response):
    """Return a Flask test response body decoded as text."""
    return response.get_data(as_text=True)


def parse_html(response_or_html):
    """Parse a response or HTML string into a semantic assertion document."""
    html = response_html(response_or_html) if hasattr(response_or_html, "get_data") else response_or_html
    parser = ParsedHTML()
    parser.feed(html)
    parser.close()
    return parser


def visible_html(response_or_html):
    """Return parser-normalized visible response text."""
    return parse_html(response_or_html).visible_text


def assert_visible_text(response_or_html, *phrases):
    """Assert that all phrases appear outside script blocks."""
    visible = visible_html(response_or_html)
    for phrase in phrases:
        assert normalize_space(phrase) in visible


def assert_not_visible_text(response_or_html, *phrases):
    """Assert that all phrases are absent outside script blocks."""
    visible = visible_html(response_or_html)
    for phrase in phrases:
        assert normalize_space(phrase) not in visible


def element_matches(element, tag=None, attrs=None, text=None):
    """Return whether a parsed element matches semantic criteria."""
    if tag is not None and element.tag != tag.lower():
        return False

    for name, expected in normalized_attrs(attrs).items():
        if not attr_matches(element.attrs, name, expected):
            return False

    if text is not None and normalize_space(text) not in element.text:
        return False

    return True


def normalized_attrs(attrs):
    """Normalize optional assertion attributes."""
    return {str(key).replace("_", "-"): value for key, value in (attrs or {}).items()}


def attr_matches(attrs, name, expected):
    """Return whether element attributes match an expected value."""
    present = name in attrs
    if expected is True:
        return present
    if expected is False:
        return not present
    if not present:
        return False
    actual = attrs.get(name)
    if name == "class":
        return str(expected) in str(actual or "").split()
    return actual == str(expected)


def assert_has_element(response_or_html, tag, attrs=None, text=None):
    """Assert that a matching parsed element exists."""
    document = parse_html(response_or_html)
    assert document.has_element(tag=tag, attrs=attrs, text=text)


def assert_no_element(response_or_html, tag, attrs=None, text=None):
    """Assert that no matching parsed element exists."""
    document = parse_html(response_or_html)
    assert not document.has_element(tag=tag, attrs=attrs, text=text)


def assert_link(response_or_html, href, text=None):
    """Assert that a link with the expected href and optional text exists."""
    assert_has_element(response_or_html, "a", attrs={"href": href}, text=text)


def assert_form(response_or_html, action, method=None, text=None):
    """Assert that a form with the expected action and optional method exists."""
    attrs = {"action": action}
    if method is not None:
        attrs["method"] = method
    assert_has_element(response_or_html, "form", attrs=attrs, text=text)


def assert_input(response_or_html, name=None, value=None, checked=None):
    """Assert that an input with expected name, value, and checked state exists."""
    attrs = {}
    if name is not None:
        attrs["name"] = name
    if value is not None:
        attrs["value"] = value
    if checked is not None:
        attrs["checked"] = checked
    assert_has_element(response_or_html, "input", attrs=attrs)


def assert_option(response_or_html, value=None, text=None, selected=None):
    """Assert that an option with expected value, text, and selection exists."""
    attrs = {}
    if value is not None:
        attrs["value"] = value
    if selected is not None:
        attrs["selected"] = selected
    assert_has_element(response_or_html, "option", attrs=attrs, text=text)


def asset_reference_values(response_or_html):
    """Return parsed ``src`` and ``href`` asset references from HTML."""
    document = parse_html(response_or_html)
    values = []
    for element in document.elements:
        for attr_name in ("src", "href"):
            value = element.attrs.get(attr_name)
            if value:
                values.append(value)
    return values


def assert_asset_reference(response_or_html, pattern):
    """Assert that a parsed asset reference matches a regular expression."""
    assert any(re.search(pattern, value) for value in asset_reference_values(response_or_html))


def asset_reference_index(response_or_html, pattern):
    """Return the first parsed asset reference index matching a regex."""
    for index, value in enumerate(asset_reference_values(response_or_html)):
        if re.search(pattern, value):
            return index
    raise AssertionError(f"No asset reference matched {pattern!r}")


def assert_no_asset_reference(response_or_html, snippet):
    """Assert that parsed asset references do not contain a snippet."""
    assert all(snippet not in value for value in asset_reference_values(response_or_html))


def assert_markup(response_or_html, *snippets):
    """Assert that all raw HTML snippets appear in the response markup."""
    html = response_html(response_or_html) if hasattr(response_or_html, "get_data") else response_or_html
    for snippet in snippets:
        assert snippet in html


def assert_not_markup(response_or_html, *snippets):
    """Assert that all raw HTML snippets are absent from the response markup."""
    html = response_html(response_or_html) if hasattr(response_or_html, "get_data") else response_or_html
    for snippet in snippets:
        assert snippet not in html
