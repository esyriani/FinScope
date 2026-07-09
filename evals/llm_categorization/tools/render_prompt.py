"""Render offline LLM categorization prompt payloads without model calls.

The renderer combines a prompt-candidate Markdown file with validated dataset
examples and emits the deterministic message payload that future prompt runners
would send to a model. It deliberately excludes expected labels and curation
metadata from the model input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evals.llm_categorization.tools.io_utils import JsonlError
from evals.llm_categorization.tools.schemas import DatasetValidationError, EvaluationExample
from evals.llm_categorization.tools.summarize_dataset import load_validated_records

EVALUATION_CONTRACT = """\
Evaluation contract:

- Treat this as a structured classification task, not a free-form assistant task.
- Use only valid taxonomy IDs supplied in the candidate taxonomy.
- Do not invent category IDs, tag IDs, category names, or tags.
- Use the UNKNOWN category when the evidence is insufficient to assign a clearer category.
- Set needs_review to true when the transaction is uncertain, ambiguous, weakly supported, or conflicting.
- A high-confidence wrong answer is worse than UNKNOWN or needs_review: true.
- Return valid JSON only, with no Markdown or extra prose.
"""

USER_INSTRUCTIONS = """\
Categorize the transaction in the input payload below.

The input payload contains:
- transaction: the transaction fields available to the classifier;
- candidate_taxonomy: the only category and tag IDs that may be returned;
- similar_transactions: optional local evidence that may support or weaken an assignment.

Required JSON output format:

{
  "request_id": "copy the input request_id exactly",
  "category_id": "one candidate category id",
  "tag_ids": ["zero or more candidate tag ids"],
  "confidence": 0.0,
  "needs_review": true,
  "supported_by_similar_transactions": false,
  "reason": "short evidence summary"
}
"""


def load_prompt(path: Path) -> str:
    """Load a prompt candidate Markdown file."""
    return path.read_text(encoding="utf-8")


def render_model_input(example: EvaluationExample) -> dict[str, Any]:
    """Return the exact example payload supplied to the model, excluding expected labels."""
    return {
        "request_id": example.request_id,
        "transaction": {
            "description": example.transaction.description,
            "merchant": example.transaction.merchant,
            "amount": example.transaction.amount,
            "date": example.transaction.date,
            "account": example.transaction.account,
            "statement_type": example.transaction.statement_type,
        },
        "candidate_taxonomy": {
            "categories": [
                {
                    "id": item.id,
                    "name": item.name,
                    "description": item.description,
                    "instruction": item.instruction,
                }
                for item in example.candidate_taxonomy.categories
            ],
            "tags": [
                {
                    "id": item.id,
                    "name": item.name,
                    "description": item.description,
                    "instruction": item.instruction,
                }
                for item in example.candidate_taxonomy.tags
            ],
        },
        "similar_transactions": [
            {
                "description": similar.description,
                "amount": similar.amount,
                "category_id": similar.category_id,
                "tag_ids": list(similar.tag_ids),
                "evidence_type": similar.evidence_type,
                "confidence": similar.confidence,
            }
            for similar in example.similar_transactions
        ],
    }


def render_message_payload(prompt_markdown: str, example: EvaluationExample) -> dict[str, Any]:
    """Return the deterministic chat message payload for one example."""
    model_input = render_model_input(example)
    validate_rendered_taxonomy_ids(model_input, example)
    user_content = "\n".join(
        [
            USER_INSTRUCTIONS.rstrip(),
            "",
            "Input payload:",
            "",
            "```json",
            json.dumps(model_input, ensure_ascii=True, indent=2, sort_keys=False),
            "```",
        ]
    )
    return {
        "messages": [
            {"role": "system", "content": f"{prompt_markdown.rstrip()}\n\n{EVALUATION_CONTRACT.rstrip()}"},
            {"role": "user", "content": user_content},
        ]
    }


def validate_rendered_taxonomy_ids(rendered_input: Mapping[str, Any], example: EvaluationExample) -> None:
    """Validate rendered taxonomy IDs match the selected dataset example exactly."""
    candidate_taxonomy = rendered_input.get("candidate_taxonomy")
    if not isinstance(candidate_taxonomy, Mapping):
        raise ValueError("rendered input is missing candidate_taxonomy")

    categories = candidate_taxonomy.get("categories")
    tags = candidate_taxonomy.get("tags")
    if not isinstance(categories, list) or not isinstance(tags, list):
        raise ValueError("rendered candidate_taxonomy categories and tags must be lists")

    rendered_category_ids = [item.get("id") for item in categories if isinstance(item, Mapping)]
    rendered_tag_ids = [item.get("id") for item in tags if isinstance(item, Mapping)]
    expected_category_ids = [item.id for item in example.candidate_taxonomy.categories]
    expected_tag_ids = [item.id for item in example.candidate_taxonomy.tags]
    if rendered_category_ids != expected_category_ids:
        raise ValueError("rendered category IDs do not match the dataset example")
    if rendered_tag_ids != expected_tag_ids:
        raise ValueError("rendered tag IDs do not match the dataset example")

    valid_category_ids = set(expected_category_ids)
    valid_tag_ids = set(expected_tag_ids)
    similar_transactions = rendered_input.get("similar_transactions")
    if not isinstance(similar_transactions, list):
        raise ValueError("rendered input similar_transactions must be a list")
    for index, similar in enumerate(similar_transactions):
        if not isinstance(similar, Mapping):
            raise ValueError(f"rendered similar_transactions[{index}] must be an object")
        category_id = similar.get("category_id")
        if category_id not in valid_category_ids:
            raise ValueError(f"rendered similar_transactions[{index}].category_id is not a valid category ID")
        tag_ids = similar.get("tag_ids")
        if not isinstance(tag_ids, list):
            raise ValueError(f"rendered similar_transactions[{index}].tag_ids must be a list")
        invalid_tag_ids = sorted(str(tag_id) for tag_id in tag_ids if tag_id not in valid_tag_ids)
        if invalid_tag_ids:
            raise ValueError(
                f"rendered similar_transactions[{index}].tag_ids contains invalid tag IDs: "
                f"{', '.join(invalid_tag_ids)}"
            )


def find_example_by_request_id(examples: Sequence[EvaluationExample], request_id: str) -> EvaluationExample:
    """Return the requested example or raise a clear error."""
    for example in examples:
        if example.request_id == request_id:
            return example
    raise ValueError(f"request_id not found in dataset: {request_id}")


def select_examples(
    examples: Sequence[EvaluationExample],
    *,
    request_id: str | None,
    dry_run_count: int | None,
) -> tuple[EvaluationExample, ...]:
    """Select either one request ID or the first N examples for dry-run rendering."""
    if request_id is not None:
        return (find_example_by_request_id(examples, request_id),)
    if dry_run_count is None:
        raise ValueError("either --request-id or --dry-run must be provided")
    if dry_run_count <= 0:
        raise ValueError("--dry-run must be positive")
    return tuple(examples[:dry_run_count])


def render_output_document(
    *,
    prompt_path: Path,
    dataset_path: Path,
    prompt_markdown: str,
    examples: Sequence[EvaluationExample],
) -> dict[str, Any]:
    """Return a deterministic render report containing message payloads."""
    return {
        "prompt": str(prompt_path),
        "dataset": str(dataset_path),
        "rendered_count": len(examples),
        "rendered_requests": [
            {
                "request_id": example.request_id,
                "message_payload": render_message_payload(prompt_markdown, example),
            }
            for example in examples
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Render LLM categorization prompt payloads without API calls.")
    parser.add_argument("--prompt", required=True, type=Path, help="Prompt candidate Markdown file.")
    parser.add_argument("--dataset", required=True, type=Path, help="Validated JSONL dataset.")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--request-id", help="Render one dataset example by request_id.")
    selection.add_argument("--dry-run", type=int, metavar="N", help="Render the first N dataset examples.")
    parser.add_argument("--out", type=Path, help="Optional output path. Prints to stdout when omitted.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the prompt renderer CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        prompt_markdown = load_prompt(args.prompt)
        _, examples = load_validated_records(args.dataset)
        selected_examples = select_examples(examples, request_id=args.request_id, dry_run_count=args.dry_run)
        document = render_output_document(
            prompt_path=args.prompt,
            dataset_path=args.dataset,
            prompt_markdown=prompt_markdown,
            examples=selected_examples,
        )
        rendered_text = json.dumps(document, ensure_ascii=True, indent=2, sort_keys=False)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(f"{rendered_text}\n", encoding="utf-8", newline="\n")
        else:
            print(rendered_text)
    except (DatasetValidationError, JsonlError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
