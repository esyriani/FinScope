"""Route tests for the developer Prompt Lab scaffold."""

import json
from dataclasses import replace
from pathlib import Path

from tests.support.html import assert_has_element, assert_no_element, assert_visible_text
from tests.support.web import set_csrf_token

from finance_app.core.constants import PROJECT_DIR
from finance_app.modules.prompt_lab import service as prompt_lab_service

NOTICE = (
    "Prompt Lab is a local developer tool. It reads and writes eval artifacts under "
    "evals/llm_categorization and does not modify production transactions, taxonomy, rules, or finscope.db."
)


def taxonomy() -> dict[str, object]:
    """Return a candidate taxonomy for Prompt Lab route tests."""
    return {
        "categories": [
            {"id": "cat_unknown", "name": "UNKNOWN", "description": "Unresolved.", "instruction": None},
            {"id": "cat_food", "name": "Food", "description": "Food purchases.", "instruction": None},
        ],
        "tags": [
            {
                "id": "tag_reimbursable",
                "name": "Reimbursable",
                "description": "Expense to reimburse.",
                "instruction": None,
            }
        ],
    }


def example(request_id: str, *, amount: float = 23.45) -> dict[str, object]:
    """Return one valid synthetic eval example."""
    return {
        "request_id": request_id,
        "transaction": {
            "description": "CORNER CAFE",
            "merchant": "Corner cafe",
            "amount": amount,
            "date": "2026-05-03",
            "account": "Credit card",
            "statement_type": "credit_card",
        },
        "candidate_taxonomy": taxonomy(),
        "similar_transactions": [],
        "expected": {"category_id": "cat_food", "tag_ids": ["tag_reimbursable"], "needs_review": True},
        "label_source": "reviewed",
        "privacy_level": "synthetic",
        "coverage": {
            "category": "Food",
            "tags": ["Reimbursable"],
            "direction": "debit" if amount > 0 else "credit",
            "statement_type": "credit_card",
            "confidence_band": "high",
            "ambiguity_type": "straightforward",
        },
        "notes": "Synthetic route fixture.",
    }


def model_output(
    request_id: str,
    *,
    category_id: str = "cat_food",
    tag_ids: list[str] | None = None,
    confidence: float = 0.97,
    needs_review: bool = True,
) -> dict[str, object]:
    """Return a model output record for Prompt Lab run tests."""
    return {
        "request_id": request_id,
        "category_id": category_id,
        "tag_ids": ["tag_reimbursable"] if tag_ids is None else tag_ids,
        "confidence": confidence,
        "needs_review": needs_review,
        "supported_by_similar_transactions": False,
        "reason": "Route test model reason.",
    }


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    """Write JSONL records for Prompt Lab route tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, sort_keys=True) for record in records), encoding="utf-8")


def write_prompt(path: Path, content: str = "# Prompt\n\nReturn JSON only.") -> None:
    """Write a prompt file for Prompt Lab route tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_raw_outputs(path: Path, rows: list[dict[str, object]]) -> None:
    """Write raw output wrappers for Prompt Lab run tests."""
    write_jsonl(
        path,
        [
            {
                "request_id": str(row["request_id"]),
                "raw_output": json.dumps(row, sort_keys=True),
                "model": "gpt-test",
                "prompt_id": "001_base",
            }
            for row in rows
        ],
    )


def write_run_config(
    run_dir: Path,
    *,
    run_id: str,
    prompt_path: Path,
    dataset_path: Path,
    dataset_hash: str = "dataset123",
) -> None:
    """Write a minimal Prompt Lab run config artifact."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "prompt_path": str(prompt_path),
                "dataset_path": str(dataset_path),
                "dataset_hash": dataset_hash,
                "model": "gpt-test",
                "temperature": 0,
                "number_of_examples": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def configure_eval_artifact_dirs(monkeypatch, tmp_path):
    """Point eval services at temporary artifact directories."""
    from evals.llm_categorization.services import dataset_service, prompt_service, run_service

    datasets_dir = tmp_path / "datasets"
    prompts_dir = tmp_path / "prompts"
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(dataset_service, "DATASETS_DIR", datasets_dir)
    monkeypatch.setattr(prompt_service, "PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr(run_service, "RUNS_DIR", runs_dir)
    return datasets_dir, prompts_dir, runs_dir


def rendered_prompt_text(response) -> str:
    """Extract rendered prompt textarea content from a response."""
    body = response.get_data(as_text=True)
    marker = 'id="rendered-prompt"'
    start = body.index(marker)
    textarea_start = body.index(">", start) + 1
    textarea_end = body.index("</textarea>", textarea_start)
    return body[textarea_start:textarea_end]


def test_prompt_lab_overview_renders_for_owner_in_development(owner_client):
    """Verify the owner-only Prompt Lab overview renders with the safety notice."""
    response = owner_client.get("/admin/prompt-lab")

    assert response.status_code == 200
    assert_visible_text(response, "Prompt Lab")
    assert_visible_text(response, NOTICE)
    assert_has_element(response, "a", attrs={"href": "/admin/prompt-lab/datasets"}, text="Datasets")
    assert_visible_text(response, "Evaluate LLM categorization prompts against labeled transaction datasets.")
    assert_visible_text(response, "Typical workflow")


def test_prompt_lab_placeholder_pages_render_for_owner(owner_client):
    """Verify the initial Prompt Lab placeholder routes are registered."""
    expected_headings = {
        "/admin/prompt-lab/datasets": "Prompt Lab datasets",
        "/admin/prompt-lab/prompts": "Prompt Lab prompts",
        "/admin/prompt-lab/runs": "Prompt Lab runs",
    }

    for path, heading in expected_headings.items():
        response = owner_client.get(path)

        assert response.status_code == 200
        assert_visible_text(response, heading)
        assert_visible_text(response, NOTICE)


def test_prompt_lab_prompts_list_shows_prompt_actions_and_run_counts(owner_client, monkeypatch, tmp_path):
    """Verify prompts list reads Markdown files and displays actions."""
    _, prompts_dir, runs_dir = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    write_prompt(prompts_dir / "001_base.md")
    (runs_dir / "run-one").mkdir(parents=True)
    (runs_dir / "run-one" / "config.json").write_text(
        json.dumps({"run_id": "run-one", "prompt_path": str(prompts_dir / "001_base.md")}),
        encoding="utf-8",
    )

    response = owner_client.get("/admin/prompt-lab/prompts")

    assert response.status_code == 200
    assert_visible_text(response, "Prompt", "Last modified", "Runs", "Actions", "001_base.md")
    assert_has_element(response, "a", attrs={"href": "/admin/prompt-lab/prompts/001_base.md"}, text="Edit")
    assert_has_element(response, "a", attrs={"href": "/admin/prompt-lab/prompts/preview?prompt=001_base.md"})
    assert_has_element(response, "a", attrs={"href": "/admin/prompt-lab/runs/new?prompt=001_base.md"}, text="Run")


def test_prompt_lab_prompt_editor_saves_existing_prompt(owner_client, monkeypatch, tmp_path):
    """Verify editor save writes only after the Save POST."""
    _, prompts_dir, _ = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    prompt_path = prompts_dir / "001_base.md"
    write_prompt(prompt_path, "# Original")
    csrf_token = set_csrf_token(owner_client)

    editor_response = owner_client.get("/admin/prompt-lab/prompts/001_base.md")
    save_response = owner_client.post(
        "/admin/prompt-lab/prompts/001_base.md/save",
        data={"csrf_token": csrf_token, "prompt_content": "# Updated"},
        follow_redirects=True,
    )

    assert editor_response.status_code == 200
    assert_visible_text(editor_response, "Prompt file: 001_base.md", "Save as new prompt")
    assert prompt_path.read_text(encoding="utf-8") == "# Updated"
    assert_visible_text(save_response, "Prompt saved.")


def test_prompt_lab_prompt_save_as_requires_md_and_confirms_overwrite(owner_client, monkeypatch, tmp_path):
    """Verify save-as validation preserves editor content and protects existing prompts."""
    _, prompts_dir, _ = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    write_prompt(prompts_dir / "001_base.md", "# Base")
    write_prompt(prompts_dir / "002_copy.md", "# Existing")
    csrf_token = set_csrf_token(owner_client)

    invalid_response = owner_client.post(
        "/admin/prompt-lab/prompts/save-as",
        data={
            "csrf_token": csrf_token,
            "source_prompt_name": "001_base.md",
            "new_prompt_name": "002_copy.txt",
            "prompt_content": "# Draft content",
        },
    )
    existing_response = owner_client.post(
        "/admin/prompt-lab/prompts/save-as",
        data={
            "csrf_token": csrf_token,
            "source_prompt_name": "001_base.md",
            "new_prompt_name": "002_copy.md",
            "prompt_content": "# Draft content",
        },
    )
    overwrite_response = owner_client.post(
        "/admin/prompt-lab/prompts/save-as",
        data={
            "csrf_token": csrf_token,
            "source_prompt_name": "001_base.md",
            "new_prompt_name": "002_copy.md",
            "prompt_content": "# Draft content",
            "overwrite_confirm": "on",
        },
        follow_redirects=True,
    )

    assert invalid_response.status_code == 200
    assert_visible_text(invalid_response, "prompt name must end with .md", "# Draft content")
    assert existing_response.status_code == 200
    assert_visible_text(existing_response, "Prompt already exists. Confirm overwrite to replace it.")
    assert overwrite_response.status_code == 200
    assert_visible_text(overwrite_response, "Prompt saved as 002_copy.md.")
    assert (prompts_dir / "002_copy.md").read_text(encoding="utf-8") == "# Draft content"


def test_prompt_lab_prompt_preview_renders_without_expected_in_model_input(owner_client, monkeypatch, tmp_path):
    """Verify preview renders deterministically without including expected labels in model input."""
    datasets_dir, prompts_dir, _ = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    write_prompt(prompts_dir / "001_base.md", "# Prompt\n\nReturn JSON only.")
    write_jsonl(datasets_dir / "valid.jsonl", [example("req-1")])
    csrf_token = set_csrf_token(owner_client)

    response = owner_client.post(
        "/admin/prompt-lab/prompts/preview",
        data={
            "csrf_token": csrf_token,
            "prompt": "001_base.md",
            "dataset": "valid.jsonl",
            "request_id": "req-1",
        },
    )

    assert response.status_code == 200
    assert_visible_text(response, "Rendered prompt", "Input example JSON", "Show expected label")
    assert '"expected"' not in rendered_prompt_text(response)
    assert '"label_source"' not in rendered_prompt_text(response)
    assert_visible_text(response, '"expected"', '"category_id": "cat_food"')


def test_prompt_lab_prompt_preview_warns_for_invalid_dataset(owner_client, monkeypatch, tmp_path):
    """Verify preview validates datasets before rendering."""
    datasets_dir, prompts_dir, _ = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    write_prompt(prompts_dir / "001_base.md")
    write_jsonl(datasets_dir / "invalid.jsonl", [example("dup"), example("dup")])
    csrf_token = set_csrf_token(owner_client)

    response = owner_client.post(
        "/admin/prompt-lab/prompts/preview",
        data={"csrf_token": csrf_token, "prompt": "001_base.md", "dataset": "invalid.jsonl", "request_id": "dup"},
    )

    assert response.status_code == 200
    assert_visible_text(response, "Dataset is invalid. Fix validation errors before rendering a preview.")


def test_prompt_lab_prompt_name_rejects_path_traversal(owner_client, monkeypatch, tmp_path):
    """Verify prompt editor routes reject names that escape the prompts directory."""
    configure_eval_artifact_dirs(monkeypatch, tmp_path)

    response = owner_client.get("/admin/prompt-lab/prompts/..%5Csecret.md")

    assert response.status_code == 404


def test_prompt_lab_dataset_list_and_detail_show_validation_summary(owner_client, monkeypatch, tmp_path):
    """Verify dataset list and detail pages read JSONL artifacts safely."""
    datasets_dir, _, _ = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    write_jsonl(datasets_dir / "valid.jsonl", [example("req-1"), example("req-2", amount=-12.0)])
    write_jsonl(datasets_dir / "invalid.jsonl", [example("dup"), example("dup")])

    list_response = owner_client.get("/admin/prompt-lab/datasets")
    detail_response = owner_client.get("/admin/prompt-lab/datasets/valid.jsonl")
    invalid_response = owner_client.get("/admin/prompt-lab/datasets/invalid.jsonl")

    assert list_response.status_code == 200
    assert_visible_text(
        list_response,
        "Dataset",
        "Examples",
        "Valid",
        "Categories",
        "Tags",
        "Needs review",
        "UNKNOWN",
        "Actions",
    )
    assert_has_element(list_response, "a", attrs={"href": "/admin/prompt-lab/datasets/valid.jsonl"}, text="View")
    assert_has_element(
        list_response,
        "form",
        attrs={"action": "/admin/prompt-lab/datasets/valid.jsonl/validate", "method": "post"},
    )
    assert detail_response.status_code == 200
    assert_visible_text(
        detail_response,
        "Dataset: valid.jsonl",
        "Request ID",
        "CORNER CAFE",
        "Food",
        "Reimbursable",
        "reviewed",
    )
    assert invalid_response.status_code == 200
    assert_visible_text(invalid_response, "Validation errors", "duplicate request_id")


def test_prompt_lab_dataset_validate_post_renders_result(owner_client, monkeypatch, tmp_path):
    """Verify validation POST is read-only and renders success or error status."""
    datasets_dir, _, _ = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    write_jsonl(datasets_dir / "valid.jsonl", [example("req-1")])
    write_jsonl(datasets_dir / "invalid.jsonl", [example("dup"), example("dup")])
    csrf_token = set_csrf_token(owner_client)

    valid_response = owner_client.post(
        "/admin/prompt-lab/datasets/valid.jsonl/validate",
        data={"csrf_token": csrf_token},
    )
    invalid_response = owner_client.post(
        "/admin/prompt-lab/datasets/invalid.jsonl/validate",
        data={"csrf_token": csrf_token},
    )

    assert valid_response.status_code == 200
    assert_visible_text(valid_response, "Validation completed successfully.")
    assert invalid_response.status_code == 200
    assert_visible_text(invalid_response, "Validation found errors.", "duplicate request_id")


def test_prompt_lab_dataset_name_rejects_path_traversal(owner_client, monkeypatch, tmp_path):
    """Verify dataset routes reject names that escape the datasets directory."""
    configure_eval_artifact_dirs(monkeypatch, tmp_path)

    response = owner_client.get("/admin/prompt-lab/datasets/..%5Csecret.jsonl")

    assert response.status_code == 404


def test_prompt_lab_new_run_form_dry_run_creates_artifacts(owner_client, monkeypatch, tmp_path):
    """Verify the new-run form launches a dry run without requiring an API key."""
    datasets_dir, prompts_dir, runs_dir = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    write_prompt(prompts_dir / "001_base.md")
    write_jsonl(datasets_dir / "validation.jsonl", [example("req-1")])
    csrf_token = set_csrf_token(owner_client)

    form_response = owner_client.get("/admin/prompt-lab/runs/new?prompt=001_base.md&dataset=validation.jsonl")
    dry_run_response = owner_client.post(
        "/admin/prompt-lab/runs/new",
        data={
            "csrf_token": csrf_token,
            "prompt": "001_base.md",
            "dataset": "validation.jsonl",
            "model": "gpt-test",
            "temperature": "0",
            "limit": "1",
            "run_name": "dry_run_test",
            "score_auto": "on",
            "run_action": "dry_run",
        },
        follow_redirects=True,
    )

    assert form_response.status_code == 200
    assert_visible_text(form_response, "New evaluation run", "Dataset valid", "Examples to run")
    assert dry_run_response.status_code == 200
    assert_visible_text(dry_run_response, "Dry run completed.", "Run: dry_run_test", "This run is not scored yet.")
    assert (runs_dir / "dry_run_test" / "rendered_prompts.jsonl").exists()
    assert (runs_dir / "dry_run_test" / "config.json").exists()


def test_prompt_lab_start_run_rejects_missing_api_key(app, owner_client, monkeypatch, tmp_path):
    """Verify real runs are blocked when the API key is missing."""
    datasets_dir, prompts_dir, _ = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    write_prompt(prompts_dir / "001_base.md")
    write_jsonl(datasets_dir / "validation.jsonl", [example("req-1")])
    app.config["FINANCE_SETTINGS"] = replace(app.config["FINANCE_SETTINGS"], openai_api_key="")
    csrf_token = set_csrf_token(owner_client)

    response = owner_client.post(
        "/admin/prompt-lab/runs/new",
        data={
            "csrf_token": csrf_token,
            "prompt": "001_base.md",
            "dataset": "validation.jsonl",
            "model": "gpt-test",
            "temperature": "0",
            "run_name": "blocked_run",
            "score_auto": "on",
            "run_action": "start",
        },
    )

    assert response.status_code == 200
    assert_visible_text(response, "Start run is blocked by preflight checks.", "API key is missing.")


def test_prompt_lab_runs_list_and_detail_show_metrics_and_failures(owner_client, monkeypatch, tmp_path):
    """Verify run list and detail pages show scored metrics and failure details."""
    from evals.llm_categorization.services import scoring_service

    datasets_dir, prompts_dir, runs_dir = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    dataset_path = datasets_dir / "validation.jsonl"
    prompt_path = prompts_dir / "001_base.md"
    run_dir = runs_dir / "validation_001_base"
    write_prompt(prompt_path)
    write_jsonl(dataset_path, [example("req-1")])
    write_raw_outputs(
        run_dir / "raw_outputs.jsonl",
        [model_output("req-1", category_id="cat_unknown", tag_ids=[], confidence=0.96, needs_review=False)],
    )
    write_run_config(run_dir, run_id="validation_001_base", prompt_path=prompt_path, dataset_path=dataset_path)
    scoring_service.score_outputs_file(dataset_path, run_dir / "raw_outputs.jsonl", run_dir)

    list_response = owner_client.get("/admin/prompt-lab/runs")
    detail_response = owner_client.get("/admin/prompt-lab/runs/validation_001_base")

    assert list_response.status_code == 200
    assert_visible_text(
        list_response,
        "Run",
        "Prompt",
        "Dataset",
        "Model",
        "Examples",
        "Category acc.",
        "Exact match",
        "Unsafe auto",
        "Compare selected",
        "Scored",
    )
    assert_has_element(
        list_response,
        "input",
        attrs={"name": "run_names", "value": "validation_001_base", "form": "compare-runs-form"},
    )
    assert_has_element(list_response, "a", attrs={"href": "/admin/prompt-lab/runs/validation_001_base"}, text="Open")
    assert detail_response.status_code == 200
    assert_visible_text(
        detail_response,
        "Run: validation_001_base",
        "Valid JSON",
        "Category accuracy",
        "Failure modes",
        "CORNER CAFE",
        "Route test model reason.",
        "high_confidence_wrong",
    )


def test_prompt_lab_compares_selected_scored_runs(owner_client, monkeypatch, tmp_path):
    """Verify selected scored runs can be compared without calling the API."""
    from evals.llm_categorization.services import scoring_service

    datasets_dir, prompts_dir, runs_dir = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    dataset_path = datasets_dir / "validation.jsonl"
    prompt_path = prompts_dir / "001_base.md"
    write_prompt(prompt_path)
    write_jsonl(dataset_path, [example("req-1")])

    run_a = runs_dir / "run_a"
    run_b = runs_dir / "run_b"
    write_raw_outputs(run_a / "raw_outputs.jsonl", [model_output("req-1")])
    write_raw_outputs(
        run_b / "raw_outputs.jsonl",
        [model_output("req-1", category_id="cat_unknown", tag_ids=[], confidence=0.96, needs_review=False)],
    )
    scoring_service.score_outputs_file(dataset_path, run_a / "raw_outputs.jsonl", run_a)
    scoring_service.score_outputs_file(dataset_path, run_b / "raw_outputs.jsonl", run_b)
    write_run_config(run_a, run_id="run_a", prompt_path=prompt_path, dataset_path=dataset_path)
    write_run_config(run_b, run_id="run_b", prompt_path=prompt_path, dataset_path=dataset_path, dataset_hash="other")
    csrf_token = set_csrf_token(owner_client)

    response = owner_client.post(
        "/admin/prompt-lab/runs/compare",
        data={"csrf_token": csrf_token, "run_names": ["run_a", "run_b"]},
    )

    assert response.status_code == 200
    assert_visible_text(
        response,
        "Run comparison",
        "Selected runs do not all use the same dataset hash. Compare metrics cautiously.",
        "Valid JSON",
        "Category accuracy",
        "Exact match",
        "Tag micro-F1",
        "UNKNOWN recall",
        "Unsafe auto-assignment",
        "High-confidence wrong",
        "Most accurate",
        "Safest",
        "Best tag behavior",
        "Most unsafe",
    )


def test_prompt_lab_compare_requires_two_scored_runs(owner_client, monkeypatch, tmp_path):
    """Verify compare actions require at least two selected scored runs."""
    configure_eval_artifact_dirs(monkeypatch, tmp_path)
    csrf_token = set_csrf_token(owner_client)

    response = owner_client.post(
        "/admin/prompt-lab/runs/compare",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert_visible_text(response, "Choose at least two scored runs to compare.", "Prompt Lab runs")


def test_prompt_lab_rescore_uses_existing_raw_outputs(owner_client, monkeypatch, tmp_path):
    """Verify re-score creates metrics from raw outputs without calling the API."""
    datasets_dir, prompts_dir, runs_dir = configure_eval_artifact_dirs(monkeypatch, tmp_path)
    dataset_path = datasets_dir / "validation.jsonl"
    prompt_path = prompts_dir / "001_base.md"
    run_dir = runs_dir / "unscored_run"
    write_prompt(prompt_path)
    write_jsonl(dataset_path, [example("req-1")])
    write_raw_outputs(run_dir / "raw_outputs.jsonl", [model_output("req-1")])
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "run_id": "unscored_run",
                "prompt_path": str(prompt_path),
                "dataset_path": str(dataset_path),
                "model": "gpt-test",
                "temperature": 0,
                "number_of_examples": 1,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    csrf_token = set_csrf_token(owner_client)

    detail_response = owner_client.get("/admin/prompt-lab/runs/unscored_run")
    rescore_response = owner_client.post(
        "/admin/prompt-lab/runs/unscored_run/rescore",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )

    assert detail_response.status_code == 200
    assert_visible_text(detail_response, "This run is not scored yet.")
    assert rescore_response.status_code == 200
    assert_visible_text(rescore_response, "Run re-scored.", "Category accuracy")
    assert (run_dir / "metrics.json").exists()


def test_prompt_lab_run_name_rejects_path_traversal(owner_client, monkeypatch, tmp_path):
    """Verify run routes reject names that escape the runs directory."""
    configure_eval_artifact_dirs(monkeypatch, tmp_path)

    response = owner_client.get("/admin/prompt-lab/runs/..%5Csecret")

    assert response.status_code == 404


def test_prompt_lab_rejects_non_owner(editor_client):
    """Verify Prompt Lab stays owner-only."""
    response = editor_client.get("/admin/prompt-lab")

    assert response.status_code == 403


def test_prompt_lab_navigation_and_routes_are_hidden_when_debug_disabled(app, owner_client):
    """Verify Prompt Lab is not visible or reachable outside development mode."""
    app.config["FINANCE_SETTINGS"] = replace(app.config["FINANCE_SETTINGS"], server_debug=False)

    home_response = owner_client.get("/")
    lab_response = owner_client.get("/admin/prompt-lab")

    assert home_response.status_code == 200
    assert_no_element(home_response, "a", attrs={"href": "/admin/prompt-lab"})
    assert lab_response.status_code == 404


def test_prompt_lab_adds_project_root_for_eval_imports(monkeypatch):
    """Verify Prompt Lab can import repository-local eval services from src-only launches."""
    project_path = str(Path(PROJECT_DIR))
    monkeypatch.setattr(
        prompt_lab_service.sys,
        "path",
        [entry for entry in prompt_lab_service.sys.path if entry != project_path],
    )

    prompt_lab_service.ensure_eval_services_importable()

    assert prompt_lab_service.sys.path[0] == project_path
