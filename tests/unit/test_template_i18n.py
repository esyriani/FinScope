"""Static checks for template translation catalog coverage."""

import json
from pathlib import Path

from jinja2 import Environment, nodes

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "finance_app" / "templates"
FRENCH_CATALOG = ROOT / "src" / "finance_app" / "translations" / "fr.json"

TRANSLATED_MACRO_ARGS = {
    "client_table_search": {2},
    "server_table_search": {4},
    "table_card_header": {0, 1},
    "collapsible_table_header": {1, 2},
    "empty_state": {0},
    "empty_table_row": {0},
    "pagination_controls": {1},
    "pagination_footer": {1},
}

TRANSLATED_MACRO_KWARGS = {
    "client_table_search": {"placeholder"},
    "server_table_search": {"placeholder", "button_label"},
    "table_card_header": {"title", "detail"},
    "collapsible_table_header": {"title", "description"},
    "empty_state": {"message"},
    "empty_table_row": {"message"},
    "pagination_controls": {"label"},
    "pagination_footer": {"label"},
}


def test_french_catalog_covers_template_literal_messages():
    """Verify template-owned literal messages have French translations."""
    catalog = json.loads(FRENCH_CATALOG.read_text(encoding="utf-8"))

    assert sorted(template_literal_messages() - set(catalog)) == []


def template_literal_messages():
    """Return literal template messages translated directly or through macros."""
    environment = Environment()
    messages = set()
    for path in TEMPLATES.glob("*.html"):
        template_ast = environment.parse(path.read_text(encoding="utf-8"))
        for call in template_ast.find_all(nodes.Call):
            name = call.node.name if isinstance(call.node, nodes.Name) else None
            if name == "_":
                messages.update(_literal_arg_values(call, {0}, set()))
            elif name in TRANSLATED_MACRO_ARGS:
                messages.update(_literal_arg_values(call, TRANSLATED_MACRO_ARGS[name], TRANSLATED_MACRO_KWARGS[name]))
    return messages


def _literal_arg_values(call, positional_indexes, keyword_names):
    """Return string literal values for selected Jinja call arguments."""
    values = set()
    for index in positional_indexes:
        if index < len(call.args):
            values.update(_const_string(call.args[index]))
    for keyword in call.kwargs:
        if keyword.key in keyword_names:
            values.update(_const_string(keyword.value))
    return values


def _const_string(node):
    """Return a Jinja constant string value, if present."""
    if isinstance(node, nodes.Const) and isinstance(node.value, str):
        return {node.value}
    return set()
