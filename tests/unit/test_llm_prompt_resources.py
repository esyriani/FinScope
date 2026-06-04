"""Tests for structured LLM prompt resources.

Verifies that system-prompt policy is loaded from the reviewable JSON resource
and rendered with runtime threshold values.
"""

from finance_app.modules.categories import llm_prompts


def test_llm_system_prompt_resource_is_structured_and_rendered():
    """Verify the external prompt resource renders into the system prompt."""
    prompt_spec = llm_prompts.load_llm_system_prompt_spec()

    prompt = llm_prompts.build_llm_system_prompt(
        [{"id": 2, "name": "Utilities", "description": "", "instruction": ""}],
        [],
        verify_threshold=0.91,
        review_threshold=0.62,
    )

    assert prompt_spec["role"] == "You are a financial transaction categorization engine."
    assert [section["heading"] for section in prompt_spec["sections"]] == [
        "Task",
        "Bank statement context",
        "Decision rules",
        "Confidence scoring",
        "Review rule",
        "Output requirements",
    ]
    assert "At or above 0.91" in prompt
    assert "0.62 up to below 0.91" in prompt
    assert "${verify_threshold}" not in prompt
    assert "Bank statement context" in prompt
    assert '"category_id": "one ID from taxonomy.categories"' in prompt
