"""Context assembly for the developer Prompt Lab feature.

This module reads file-based eval artifacts through the lightweight eval
services. It does not access FinScope transactions, taxonomy, rules, or runtime
databases.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_app.core.constants import PROJECT_DIR

PROMPT_LAB_NOTICE = (
    "Prompt Lab is a local developer tool. It reads and writes eval artifacts under "
    "evals/llm_categorization and does not modify production transactions, taxonomy, rules, or finscope.db."
)
DEFAULT_RUN_MODEL = "gpt-5-mini"
DEFAULT_RUN_TEMPERATURE = "0"
DEFAULT_RUN_TIMEOUT_SECONDS = 60


def ensure_eval_services_importable() -> None:
    """Make repository-local eval services importable for the developer UI."""
    project_path = str(Path(PROJECT_DIR))
    if project_path not in sys.path:
        sys.path.insert(0, project_path)


def build_overview_context() -> dict[str, Any]:
    """Build Prompt Lab overview page context from eval artifact directories."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_service import read_dataset_summaries
    from evals.llm_categorization.services.prompt_service import list_prompt_files
    from evals.llm_categorization.services.run_service import list_runs

    dataset_results = read_dataset_summaries()
    prompts = list_prompt_files()
    runs = list_runs()
    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "dataset_count": len(dataset_results),
        "prompt_count": len(prompts),
        "run_count": len(runs),
        "latest_dataset_status": latest_dataset_status(dataset_results),
        "latest_run": latest_run_summary(runs),
        "best_result": best_validation_result(runs),
    }


def build_datasets_context() -> dict[str, Any]:
    """Build Prompt Lab datasets placeholder page context."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_service import read_dataset_summaries

    dataset_results = read_dataset_summaries()
    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "dataset_rows": [dataset_list_row(artifact, result) for artifact, result in dataset_results],
    }


def build_dataset_detail_context(
    dataset_name: str,
    *,
    validation_result: Any | None = None,
    validation_completed: bool = False,
) -> dict[str, Any]:
    """Build Prompt Lab dataset detail context for one safe dataset name."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_service import (
        read_dataset_examples,
        resolve_dataset_path,
        validate_dataset_file,
    )

    dataset_path = resolve_dataset_path(dataset_name)
    result = validation_result or validate_dataset_file(dataset_path)
    examples = read_dataset_examples(dataset_path) if result.valid else ()
    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "dataset_name": dataset_name,
        "validation_completed": validation_completed,
        "validation_result": result,
        "summary_cards": dataset_summary_cards(result),
        "warnings": tuple(result.summary.warnings) if result.summary else (),
        "error_lines": result.error_lines,
        "example_rows": [dataset_example_row(example) for example in examples],
    }


def validate_dataset_by_name(dataset_name: str) -> Any:
    """Validate one safe dataset name from the Prompt Lab UI."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_service import resolve_dataset_path, validate_dataset_file

    return validate_dataset_file(resolve_dataset_path(dataset_name))


def build_dataset_builder_context(
    values: Mapping[str, Any] | None,
    *,
    default_db_path: Path,
    preview: Any | None = None,
    build_result: Any | None = None,
    errors: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the dataset-spec builder form and result context."""
    form = normalize_dataset_build_form(values or {}, default_db_path=default_db_path)
    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "form": form,
        "errors": tuple(errors),
        "label_source_options": ("prefer", "allow", "candidate_only", "exclude"),
        "category_target_rows": target_rows_for_form(form, "categories"),
        "tag_target_rows": target_rows_for_form(form, "tags"),
        "special_target_rows": special_target_rows(form),
        "preview_rows": preview_rows(preview) if preview else (),
        "build_summary": build_summary(build_result) if build_result else None,
    }


def preview_dataset_build_from_form(values: Mapping[str, Any], *, default_db_path: Path) -> Any:
    """Preview a dataset build from submitted form values without writing datasets."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_builder_service import preview_dataset_build

    spec = dataset_spec_from_form(values, default_db_path=default_db_path)
    save_dataset_builder_spec(spec)
    return preview_dataset_build(Path(str(values.get("db_path") or default_db_path)), spec)


def run_dataset_build_from_form(values: Mapping[str, Any], *, default_db_path: Path) -> Any:
    """Build draft dataset artifacts from submitted form values."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_builder_service import build_draft_dataset_from_spec

    spec = dataset_spec_from_form(values, default_db_path=default_db_path)
    save_dataset_builder_spec(spec)
    return build_draft_dataset_from_spec(Path(str(values.get("db_path") or default_db_path)), spec)


def build_labeling_queues_context() -> dict[str, Any]:
    """Build the labeling queue list page context."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.labeling_queue_service import list_labeling_queues, validate_labeling_queue

    rows = []
    for artifact in list_labeling_queues():
        result = validate_labeling_queue(artifact.path)
        items = read_jsonl_records(artifact.path)
        rows.append(
            {
                "name": artifact.name,
                "items": result.item_count,
                "pending": result.pending_count,
                "labeled": result.labeled_count,
                "unusable": result.unusable_count,
                "valid": result.valid,
                "ai_unknown": count_queue_failure(items, "ai_unknown"),
                "ai_needs_review": count_queue_failure(items, "ai_needs_review"),
                "ai_low_confidence": count_queue_failure(items, "ai_low_confidence"),
                "ai_corrected_later": count_queue_failure(items, "ai_corrected_later"),
            }
        )
    return {"prompt_lab_notice": PROMPT_LAB_NOTICE, "queue_rows": rows}


def build_labeling_queue_detail_context(queue_name: str, filters: Mapping[str, Any]) -> dict[str, Any]:
    """Build one labeling queue detail context."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.labeling_queue_service import (
        read_labeling_queue,
        resolve_labeling_queue_path,
        validate_labeling_queue,
    )

    queue_path = resolve_labeling_queue_path(queue_name)
    result = validate_labeling_queue(queue_path)
    selected_filter = str(filters.get("filter") or "")
    items = [item for item in read_labeling_queue(queue_path) if queue_item_matches_filter(item, selected_filter)]
    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "queue_name": queue_name,
        "validation_result": result,
        "selected_filter": selected_filter,
        "filter_options": labeling_filter_options(),
        "queue_rows": [labeling_queue_row(item) for item in items],
        "export_name": exported_labeling_dataset_name(queue_name),
    }


def build_labeling_item_context(
    queue_name: str,
    request_id: str,
    *,
    values: Mapping[str, Any] | None = None,
    errors: Sequence[str] = (),
) -> dict[str, Any]:
    """Build one labeling queue item page context."""
    item = labeling_item_by_request_id(queue_name, request_id)
    form = normalize_labeling_item_form(item, values)
    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "queue_name": queue_name,
        "request_id": request_id,
        "item": item,
        "transaction": mapping_value(item.get("transaction")),
        "ai_observation": mapping_value(item.get("ai_observation")),
        "category_options": taxonomy_options(item, "categories"),
        "tag_options": taxonomy_options(item, "tags"),
        "form": form,
        "errors": tuple(errors),
    }


def save_labeling_item_from_form(queue_name: str, request_id: str, values: Mapping[str, Any]) -> str | None:
    """Save a manual label for one queue item and return the next pending request ID."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.labeling_queue_service import (
        read_labeling_queue,
        resolve_labeling_queue_path,
        save_manual_label,
    )

    queue_path = resolve_labeling_queue_path(queue_name)
    tag_ids = form_getlist(values, "tag_ids")
    save_manual_label(
        queue_path,
        request_id,
        category_id=str(values.get("category_id") or ""),
        tag_ids=tag_ids,
        needs_review=parse_form_bool(str(values.get("needs_review") or "")),
        label_source=str(values.get("label_source") or "curated_by_researcher"),
        notes=str(values.get("notes") or ""),
    )
    for item in read_labeling_queue(queue_path):
        if item.get("label_status") == "pending":
            return str(item.get("request_id"))
    return None


def mark_labeling_item_unusable_from_form(queue_name: str, request_id: str, values: Mapping[str, Any]) -> None:
    """Mark one labeling queue item unusable."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.labeling_queue_service import (
        mark_queue_item_unusable,
        resolve_labeling_queue_path,
    )

    reason = str(values.get("unusable_reason") or values.get("notes") or "Marked unusable from Prompt Lab.")
    mark_queue_item_unusable(resolve_labeling_queue_path(queue_name), request_id, reason=reason)


def export_labeling_queue_from_form(queue_name: str) -> str:
    """Export labeled queue items and return the generated dataset name."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services import DATASETS_DIR
    from evals.llm_categorization.services.labeling_queue_service import (
        export_labeled_queue,
        resolve_labeling_queue_path,
    )

    output_name = exported_labeling_dataset_name(queue_name)
    out_path = DATASETS_DIR / output_name
    export_labeled_queue(resolve_labeling_queue_path(queue_name), out_path)
    return output_name


def build_prompts_context() -> dict[str, Any]:
    """Build Prompt Lab prompts list page context."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.prompt_service import list_prompt_files
    from evals.llm_categorization.services.run_service import list_runs

    prompts = list_prompt_files()
    runs = list_runs()

    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "prompt_rows": [prompt_list_row(prompt, runs) for prompt in prompts],
    }


def build_prompt_editor_context(
    prompt_name: str,
    *,
    prompt_content: str | None = None,
    new_prompt_name: str = "",
    show_overwrite_warning: bool = False,
) -> dict[str, Any]:
    """Build Prompt Lab prompt editor context for one safe prompt name."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.prompt_service import read_prompt_by_name, resolve_prompt_path

    prompt_path = resolve_prompt_path(prompt_name)
    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "prompt_name": prompt_name,
        "prompt_content": read_prompt_by_name(prompt_name) if prompt_content is None else prompt_content,
        "new_prompt_name": new_prompt_name,
        "show_overwrite_warning": show_overwrite_warning,
        "last_modified": format_modified_at(prompt_path.stat().st_mtime),
    }


def save_prompt_content(prompt_name: str, prompt_content: str) -> None:
    """Save edited content to an existing prompt file."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.prompt_service import save_prompt_by_name

    save_prompt_by_name(prompt_name, prompt_content)


def save_prompt_copy(prompt_name: str, prompt_content: str, *, overwrite: bool) -> str:
    """Save edited content as another prompt file and return the saved name."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.prompt_service import save_prompt_as

    saved_path = save_prompt_as(prompt_name, prompt_content, overwrite=overwrite)
    return saved_path.name


def build_prompt_preview_context(
    *,
    selected_prompt: str = "",
    selected_dataset: str = "",
    selected_request_id: str = "",
    render: bool = False,
) -> dict[str, Any]:
    """Build Prompt Lab prompt preview context and optionally render a prompt payload."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_service import (
        list_datasets,
        read_dataset_examples,
        resolve_dataset_path,
        validate_dataset_file,
    )
    from evals.llm_categorization.services.prompt_service import (
        list_prompt_files,
        render_prompt_preview,
        resolve_prompt_path,
    )

    prompts = list_prompt_files()
    datasets = list_datasets()
    request_choices: tuple[str, ...] = ()
    warnings: list[str] = []
    rendered_prompt_text = ""
    input_example_json = ""
    expected_label_json = ""

    dataset_valid = False
    if selected_dataset:
        try:
            dataset_path = resolve_dataset_path(selected_dataset)
        except (FileNotFoundError, ValueError):
            warnings.append("Selected dataset was not found.")
        else:
            validation_result = validate_dataset_file(dataset_path)
            if validation_result.valid:
                dataset_valid = True
                request_choices = tuple(
                    str(record.get("request_id") or "") for record in read_dataset_examples(dataset_path)
                )
                if not selected_request_id and request_choices:
                    selected_request_id = request_choices[0]
            else:
                warnings.append("Dataset is invalid. Fix validation errors before rendering a preview.")

    if render:
        if not selected_prompt or not selected_dataset or not selected_request_id:
            warnings.append("Choose a prompt, dataset, and example before rendering a preview.")
        elif dataset_valid:
            try:
                prompt_path = resolve_prompt_path(selected_prompt)
                dataset_path = resolve_dataset_path(selected_dataset)
                document = render_prompt_preview(
                    prompt_path=prompt_path,
                    dataset_path=dataset_path,
                    request_id=selected_request_id,
                )
                record = read_dataset_examples(dataset_path, request_id=selected_request_id)[0]
                rendered_prompt_text = json.dumps(
                    document["rendered_requests"][0]["message_payload"],
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=False,
                )
                input_example_json = json.dumps(model_input_from_record(record), ensure_ascii=True, indent=2)
                expected_label_json = json.dumps(expected_label_from_record(record), ensure_ascii=True, indent=2)
            except (FileNotFoundError, ValueError, IndexError, KeyError) as exc:
                warnings.append(str(exc))

    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "prompts": prompts,
        "datasets": datasets,
        "request_choices": request_choices,
        "selected_prompt": selected_prompt,
        "selected_dataset": selected_dataset,
        "selected_request_id": selected_request_id,
        "warnings": tuple(warnings),
        "rendered_prompt_text": rendered_prompt_text,
        "input_example_json": input_example_json,
        "expected_label_json": expected_label_json,
    }


def build_runs_context() -> dict[str, Any]:
    """Build Prompt Lab runs list page context."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.run_service import list_runs

    runs = list_runs()
    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "run_rows": [run_list_row(run) for run in runs],
    }


def build_run_comparison_context(run_names: Sequence[str]) -> dict[str, Any]:
    """Build a Prompt Lab comparison context for selected scored run directories."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.comparison_service import compare_selected_runs
    from evals.llm_categorization.services.run_service import resolve_run_path

    if len(run_names) < 2:
        raise ValueError("Choose at least two scored runs to compare.")

    run_paths = tuple(resolve_run_path(str(run_name)) for run_name in run_names)
    for run_path in run_paths:
        if not (run_path / "metrics.json").exists():
            raise ValueError(f"{run_path.name} is not scored.")
        if not (run_path / "scored_outputs.jsonl").exists():
            raise ValueError(f"{run_path.name} is missing scored outputs.")

    compare_selected_runs(run_paths)
    run_rows = [comparison_run_row(run_path) for run_path in run_paths]
    dataset_hashes = {row["dataset_hash"] for row in run_rows if row["dataset_hash"]}
    warnings = []
    if len(dataset_hashes) > 1:
        warnings.append("Selected runs do not all use the same dataset hash. Compare metrics cautiously.")

    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "run_rows": run_rows,
        "metric_rows": comparison_metric_rows(run_rows),
        "interpretation": comparison_interpretation(run_rows),
        "warnings": tuple(warnings),
    }


def build_new_run_context(
    values: Mapping[str, Any] | None = None,
    *,
    submitted: bool = False,
    api_key_configured: bool = False,
    errors: Sequence[str] = (),
) -> dict[str, Any]:
    """Build Prompt Lab new-run form context with preflight status."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_service import list_datasets
    from evals.llm_categorization.services.prompt_service import list_prompt_files

    form = normalize_run_form(values or {}, submitted=submitted)
    preflight = run_preflight(form, api_key_configured=api_key_configured)
    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "prompts": list_prompt_files(),
        "datasets": list_datasets(),
        "form": form,
        "preflight": preflight,
        "errors": tuple(dict.fromkeys((*errors, *preflight["errors"]))),
    }


def build_run_detail_context(run_name: str) -> dict[str, Any]:
    """Build Prompt Lab run detail context for one safe run directory."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.run_service import resolve_run_path
    from evals.llm_categorization.services.scoring_service import read_run_failures

    run_path = resolve_run_path(run_name)
    config = read_json_artifact(run_path / "config.json")
    metrics = read_json_artifact(run_path / "metrics.json") if (run_path / "metrics.json").exists() else None
    failures = read_run_failures(run_path) if (run_path / "failures.jsonl").exists() else ()
    dataset_records = dataset_records_for_run(run_path, config)
    headline = mapping_value(metrics.get("headline")) if metrics else {}
    return {
        "prompt_lab_notice": PROMPT_LAB_NOTICE,
        "run_name": run_name,
        "run_header": run_header(run_name, config, metrics),
        "not_scored": metrics is None,
        "summary_cards": run_summary_cards(headline),
        "metric_rows": run_metric_rows(headline),
        "failure_rows": [failure_row(failure, dataset_records) for failure in failures],
    }


def rescore_run_by_name(run_name: str) -> None:
    """Rescore a run from saved raw outputs without calling a model provider."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.run_service import resolve_run_path
    from evals.llm_categorization.services.scoring_service import rescore_run

    rescore_run(resolve_run_path(run_name))


def launch_prompt_lab_run(
    values: Mapping[str, Any],
    *,
    dry_run: bool,
    api_key: str | None,
    config_path: Path,
) -> str:
    """Launch one Prompt Lab eval run and return its run name."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.run_service import launch_evaluation_run

    config = eval_config_from_form(values, dry_run=dry_run, config_path=config_path)
    launch_evaluation_run(config, api_key=None if dry_run else api_key)
    return str(values["run_name"])


def eval_config_from_form(values: Mapping[str, Any], *, dry_run: bool, config_path: Path) -> Any:
    """Build an eval-runner config from validated Prompt Lab form values."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_service import resolve_dataset_path
    from evals.llm_categorization.services.prompt_service import resolve_prompt_path
    from evals.llm_categorization.services.run_service import resolve_run_path
    from evals.llm_categorization.tools import run_eval

    return run_eval.EvalConfig(
        prompt_path=resolve_prompt_path(str(values["prompt"])),
        dataset_path=resolve_dataset_path(str(values["dataset"])),
        model=str(values["model"]),
        temperature=float(values["temperature"]),
        out_dir=resolve_run_path(str(values["run_name"]), must_exist=False),
        max_output_tokens=None,
        response_format=run_eval.DEFAULT_RESPONSE_FORMAT,
        limit=values.get("limit_value"),
        request_id=None,
        resume=False,
        dry_run=dry_run,
        score=bool(values.get("score_auto")),
        retry_policy=run_eval.RetryPolicy(
            max_retries=run_eval.DEFAULT_MAX_RETRIES,
            retry_delay_seconds=run_eval.DEFAULT_RETRY_DELAY_SECONDS,
        ),
        config_path=config_path,
        timeout_seconds=DEFAULT_RUN_TIMEOUT_SECONDS,
    )


def latest_dataset_status(dataset_results: Sequence[tuple[Any, Any]]) -> dict[str, Any] | None:
    """Return the most recently modified dataset validation status."""
    if not dataset_results:
        return None
    artifact, result = max(dataset_results, key=lambda item: item[0].modified_at)
    return {
        "name": artifact.name,
        "valid": result.valid,
        "status": "Valid" if result.valid else "Invalid",
    }


def latest_run_summary(runs: Sequence[Any]) -> dict[str, Any] | None:
    """Return the most recently modified run artifact summary."""
    if not runs:
        return None
    run = max(runs, key=lambda item: item.modified_at)
    return {
        "run_id": run.run_id,
        "model": run.model,
        "prompt_id": run.prompt_id,
        "has_metrics": run.has_metrics,
    }


def best_validation_result(runs: Sequence[Any]) -> dict[str, Any] | None:
    """Return the best scored validation run by composite score when available."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.run_service import read_optional_json_object
    from evals.llm_categorization.services.scoring_service import read_run_metrics

    candidates = []
    for run in runs:
        if not run.has_metrics:
            continue
        try:
            metrics = read_run_metrics(run.path)
        except (OSError, ValueError):
            continue
        config = read_optional_json_object(run.path / "config.json")
        if not is_validation_run(run, metrics, config):
            continue
        headline = mapping_value(metrics.get("headline"))
        score = numeric_value(headline.get("composite_score"))
        if score is None:
            continue
        candidates.append((score, str(run.run_id), run, headline))
    if not candidates:
        return None
    score, _, run, headline = max(candidates)
    return {
        "run_id": run.run_id,
        "score": score,
        "category_accuracy": numeric_value(headline.get("category_accuracy")),
        "unsafe_auto_assignment_rate": numeric_value(headline.get("unsafe_auto_assignment_rate")),
    }


def is_validation_run(run: Any, metrics: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    """Return whether a run appears to target a validation dataset."""
    metric_run = mapping_value(metrics.get("run"))
    tokens = " ".join(
        str(value)
        for value in (
            run.run_id,
            config.get("dataset_path"),
            metric_run.get("dataset"),
            metric_run.get("outputs"),
        )
        if value
    ).lower()
    return "validation" in tokens


def normalize_dataset_build_form(values: Mapping[str, Any], *, default_db_path: Path) -> dict[str, Any]:
    """Return display-ready dataset build form values."""
    return {
        "name": str(values.get("name") or values.get("dataset_name") or "curated_v1").strip(),
        "description": str(values.get("description") or "").strip(),
        "max_examples": str(values.get("max_examples") or "50"),
        "seed": str(values.get("seed") or "42"),
        "redact": form_checked(values, "redact", default=True),
        "db_path": str(values.get("db_path") or default_db_path),
        "label_sources": {
            key: str(values.get(f"label_source_{key}") or default)
            for key, default in {
                "manual_edit": "prefer",
                "reviewed": "prefer",
                "high_confidence_rule": "allow",
                "stable_history": "allow",
                "ai": "candidate_only",
                "unresolved": "candidate_only",
            }.items()
        },
        "ai": {
            "include": form_checked(values, "ai_include", default=True),
            "max_examples": str(values.get("ai_max_examples") or "20"),
            "include_ai_unknown": form_checked(values, "ai_include_unknown", default=True),
            "include_ai_needs_review": form_checked(values, "ai_include_needs_review", default=True),
            "include_low_confidence": form_checked(values, "ai_include_low_confidence", default=True),
            "low_confidence_threshold": str(values.get("ai_low_confidence_threshold") or "0.85"),
            "include_ai_corrected_later": form_checked(values, "ai_include_corrected_later", default=True),
            "require_manual_label_before_export": form_checked(
                values, "ai_require_manual_label_before_export", default=True
            ),
        },
        "selection": {
            "max_per_near_duplicate_group": str(values.get("max_per_near_duplicate_group") or "2"),
            "include_full_taxonomy": form_checked(values, "include_full_taxonomy", default=True),
        },
        "targets": {
            "categories": target_pairs_from_values(values, "category"),
            "tags": target_pairs_from_values(values, "tag"),
            "special": {key: str(values.get(f"target_{key}") or "0") for key in special_target_keys()},
        },
    }


def dataset_spec_from_form(values: Mapping[str, Any], *, default_db_path: Path) -> Any:
    """Build a normalized dataset spec from Prompt Lab form values."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_builder_service import normalize_dataset_spec

    form = normalize_dataset_build_form(values, default_db_path=default_db_path)
    payload = {
        "name": form["name"],
        "description": form["description"],
        "max_examples": parse_int_text(str(form["max_examples"]), "max examples"),
        "seed": parse_int_text(str(form["seed"]), "seed"),
        "redact": bool(form["redact"]),
        "label_sources": form["label_sources"],
        "ai_problem_cases": {
            "include": form["ai"]["include"],
            "max_examples": parse_int_text(str(form["ai"]["max_examples"]), "AI max examples"),
            "include_ai_unknown": form["ai"]["include_ai_unknown"],
            "include_ai_needs_review": form["ai"]["include_ai_needs_review"],
            "include_low_confidence": form["ai"]["include_low_confidence"],
            "low_confidence_threshold": parse_float_text(
                str(form["ai"]["low_confidence_threshold"]), "AI low confidence threshold"
            ),
            "include_ai_corrected_later": form["ai"]["include_ai_corrected_later"],
            "require_manual_label_before_export": form["ai"]["require_manual_label_before_export"],
        },
        "targets": {
            "categories": target_mapping_from_pairs(form["targets"]["categories"], "category"),
            "tags": target_mapping_from_pairs(form["targets"]["tags"], "tag"),
            "directions": {
                "debit": parse_count_text(form["targets"]["special"]["debit"], "debit"),
                "credit": parse_count_text(form["targets"]["special"]["credit"], "credit"),
            },
            "review": {
                "needs_review_true": parse_count_text(
                    form["targets"]["special"]["needs_review_true"], "needs_review true"
                ),
                "needs_review_false": parse_count_text(
                    form["targets"]["special"]["needs_review_false"], "needs_review false"
                ),
            },
            "tag_shape": {
                "no_tags": parse_count_text(form["targets"]["special"]["no_tags"], "no tags"),
                "one_or_more_tags": parse_count_text(
                    form["targets"]["special"]["one_or_more_tags"], "one or more tags"
                ),
            },
            "ambiguity_types": {
                key: parse_count_text(form["targets"]["special"][key], key.replace("_", " "))
                for key in ambiguity_target_keys()
            },
        },
        "selection": {
            "max_per_near_duplicate_group": parse_int_text(
                str(form["selection"]["max_per_near_duplicate_group"]), "max per near-duplicate group"
            ),
            "prefer_recent_manual_labels": True,
            "include_full_taxonomy": bool(form["selection"]["include_full_taxonomy"]),
            "write_labeling_queue": True,
            "write_adjudication_queue": True,
        },
    }
    return normalize_dataset_spec(payload)


def save_dataset_builder_spec(spec: Any) -> None:
    """Persist a normalized dataset spec JSON artifact for later reuse."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services import DATASET_SPECS_DIR

    DATASET_SPECS_DIR.mkdir(parents=True, exist_ok=True)
    spec_path = DATASET_SPECS_DIR / f"{spec.name}.json"
    spec_payload = {
        "name": spec.name,
        "description": spec.description,
        "max_examples": spec.max_examples,
        "seed": spec.seed,
        "redact": spec.redact,
        "label_sources": spec.label_sources,
        "ai_problem_cases": spec.ai_problem_cases,
        "targets": spec.targets,
        "selection": spec.selection,
    }
    spec_path.write_text(json.dumps(spec_payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def preview_rows(preview: Any) -> tuple[dict[str, Any], ...]:
    """Return target-preview rows for display."""
    return tuple(
        {
            "target": f"{row.target_type}.{row.name}",
            "requested": row.requested,
            "eligible_found": f"{row.eligible_candidates}/{row.found_candidates}",
            "selected_possible": f"{row.possible_selected}/{row.requested}",
            "status": row.status,
        }
        for row in preview.target_previews
    )


def build_summary(result: Any) -> dict[str, Any]:
    """Return a dataset build result summary."""
    shortages = [row for row in result.target_previews if row.status != "OK"]
    return {
        "files": (
            {
                "label": "Draft dataset",
                "path": result.artifacts.dataset_path,
                "dataset_name": result.artifacts.dataset_path.name,
            },
            {"label": "Coverage report", "path": result.artifacts.coverage_report_path},
            {"label": "Adjudication queue", "path": result.artifacts.adjudication_path},
            {
                "label": "Labeling queue",
                "path": result.artifacts.labeling_queue_path,
                "queue_name": result.artifacts.labeling_queue_path.name,
            },
            {"label": "Spec snapshot", "path": result.artifacts.spec_used_path},
        ),
        "selected_count": len(result.records),
        "adjudication_count": len(result.adjudication_records),
        "labeling_queue_count": len(result.labeling_queue_records),
        "shortages": [f"{row.target_type}.{row.name}: {row.status}" for row in shortages],
        "warnings": ("Draft dataset. Manual review is recommended before using this file as validation or test data.",),
    }


def target_rows_for_form(form: Mapping[str, Any], kind: str) -> tuple[dict[str, str], ...]:
    """Return category or tag target rows for the builder form."""
    pairs = mapping_value(mapping_value(form.get("targets")).get(kind))
    rows = [{"name": str(name), "count": str(count)} for name, count in pairs.items()]
    while len(rows) < 5:
        rows.append({"name": "", "count": "0"})
    return tuple(rows)


def special_target_rows(form: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    """Return special target input rows for the builder form."""
    special = mapping_value(mapping_value(form.get("targets")).get("special"))
    labels = {
        "debit": "Debit",
        "credit": "Credit",
        "needs_review_true": "needs_review true",
        "needs_review_false": "needs_review false",
        "no_tags": "No tags",
        "one_or_more_tags": "One or more tags",
        "straightforward": "Straightforward",
        "transfer_like": "Transfer-like",
        "reimbursement_like": "Reimbursement-like",
        "reimbursable_like": "Reimbursable-like",
        "rental_like": "Rental-like",
        "tax_like": "Tax-like",
        "unknown_correct": "UNKNOWN correct",
        "ai_unknown": "AI UNKNOWN",
        "ai_needs_review": "AI needs review",
        "ai_low_confidence": "AI low confidence",
        "ai_corrected_later": "AI corrected later",
    }
    return tuple(
        {"key": key, "label": labels[key], "value": str(special.get(key) or "0")} for key in special_target_keys()
    )


def special_target_keys() -> tuple[str, ...]:
    """Return all special target field keys."""
    return (
        "debit",
        "credit",
        "needs_review_true",
        "needs_review_false",
        "no_tags",
        "one_or_more_tags",
        *ambiguity_target_keys(),
    )


def ambiguity_target_keys() -> tuple[str, ...]:
    """Return ambiguity target keys."""
    return (
        "straightforward",
        "transfer_like",
        "reimbursement_like",
        "reimbursable_like",
        "rental_like",
        "tax_like",
        "unknown_correct",
        "ai_unknown",
        "ai_needs_review",
        "ai_low_confidence",
        "ai_corrected_later",
    )


def target_pairs_from_values(values: Mapping[str, Any], prefix: str) -> dict[str, str]:
    """Return target name/count pairs from repeated form fields."""
    names = form_getlist(values, f"{prefix}_target_name")
    counts = form_getlist(values, f"{prefix}_target_count")
    pairs = {}
    for name, count in zip(names, counts, strict=False):
        stripped_name = str(name).strip()
        if stripped_name:
            pairs[stripped_name] = str(count or "0").strip() or "0"
    return pairs


def target_mapping_from_pairs(pairs: Mapping[str, Any], label: str) -> dict[str, int]:
    """Return parsed target count mapping."""
    return {str(name): parse_count_text(str(count), f"{label} target {name}") for name, count in pairs.items()}


def parse_int_text(value: str, label: str) -> int:
    """Parse an integer form value."""
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an integer") from exc


def parse_float_text(value: str, label: str) -> float:
    """Parse a float form value."""
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be numeric") from exc


def parse_count_text(value: str, label: str) -> int:
    """Parse a non-negative target count."""
    parsed = parse_int_text(value or "0", label)
    if parsed < 0:
        raise ValueError(f"{label} must be non-negative")
    return parsed


def form_checked(values: Mapping[str, Any], key: str, *, default: bool) -> bool:
    """Return checkbox state from form values."""
    if key not in values:
        return default
    submitted_values = form_getlist(values, key)
    return any(str(value or "").lower() in {"on", "true", "1", "yes"} for value in submitted_values)


def form_getlist(values: Mapping[str, Any], key: str) -> list[str]:
    """Return repeated form values from Flask MultiDict-like or plain mappings."""
    getlist = getattr(values, "getlist", None)
    if callable(getlist):
        return [str(value) for value in getlist(key)]
    value = values.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def parse_form_bool(value: str) -> bool:
    """Parse an explicit boolean select value."""
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("needs_review must be true or false")


def read_jsonl_records(path: Path) -> tuple[dict[str, Any], ...]:
    """Read JSONL records, returning an empty tuple when unavailable."""
    try:
        from evals.llm_categorization.tools.io_utils import load_jsonl

        return tuple(load_jsonl(path))
    except (OSError, ValueError):
        return ()


def count_queue_failure(items: Sequence[Mapping[str, Any]], failure_type: str) -> int:
    """Count queue items with one AI failure type."""
    return sum(1 for item in items if mapping_value(item.get("ai_observation")).get("failure_type") == failure_type)


def queue_item_matches_filter(item: Mapping[str, Any], selected_filter: str) -> bool:
    """Return whether a labeling queue item matches the selected filter."""
    if not selected_filter:
        return True
    if selected_filter in {"pending", "labeled", "unusable"}:
        return item.get("label_status") == selected_filter
    return mapping_value(item.get("ai_observation")).get("failure_type") == selected_filter


def labeling_filter_options() -> tuple[dict[str, str], ...]:
    """Return labeling queue filter options."""
    return (
        {"value": "", "label": "All"},
        {"value": "pending", "label": "Pending only"},
        {"value": "ai_unknown", "label": "AI UNKNOWN"},
        {"value": "ai_needs_review", "label": "AI needs review"},
        {"value": "ai_low_confidence", "label": "Low confidence"},
        {"value": "ai_corrected_later", "label": "Corrected later"},
        {"value": "labeled", "label": "Labeled"},
        {"value": "unusable", "label": "Unusable"},
    )


def labeling_queue_row(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return one labeling queue table row."""
    transaction = mapping_value(item.get("transaction"))
    observation = mapping_value(item.get("ai_observation"))
    return {
        "request_id": str(item.get("request_id") or ""),
        "description": str(transaction.get("description") or ""),
        "amount": transaction.get("amount"),
        "ai_category": observation.get("category_id") or "n/a",
        "ai_confidence": observation.get("confidence"),
        "ai_needs_review": observation.get("needs_review"),
        "failure_type": observation.get("failure_type") or "n/a",
        "label_status": str(item.get("label_status") or ""),
    }


def labeling_item_by_request_id(queue_name: str, request_id: str) -> dict[str, Any]:
    """Return one queue item by safe queue name and request ID."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.labeling_queue_service import (
        read_labeling_queue,
        resolve_labeling_queue_path,
    )

    validate_request_id_for_route(request_id)
    for item in read_labeling_queue(resolve_labeling_queue_path(queue_name)):
        if item.get("request_id") == request_id:
            return item
    raise ValueError(f"request_id not found: {request_id}")


def validate_request_id_for_route(request_id: str) -> None:
    """Reject request IDs that could confuse route/path handling."""
    if not request_id or "/" in request_id or "\\" in request_id or request_id in {".", ".."}:
        raise ValueError("invalid request ID")


def normalize_labeling_item_form(item: Mapping[str, Any], values: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return form values for a labeling item."""
    expected = mapping_value(item.get("expected"))
    source = values or expected
    return {
        "category_id": str(source.get("category_id") or ""),
        "tag_ids": set(form_getlist(source, "tag_ids") or list_value(expected.get("tag_ids"))),
        "needs_review": str(source.get("needs_review")).lower() if source.get("needs_review") is not None else "true",
        "label_source": str(source.get("label_source") or item.get("label_source") or "curated_by_researcher"),
        "notes": str(values.get("notes") if values else ""),
    }


def taxonomy_options(item: Mapping[str, Any], collection_name: str) -> tuple[dict[str, str], ...]:
    """Return taxonomy options from a queue item."""
    taxonomy = mapping_value(item.get("candidate_taxonomy"))
    options = []
    for taxonomy_item in list_value(taxonomy.get(collection_name)):
        if isinstance(taxonomy_item, Mapping):
            item_id = str(taxonomy_item.get("id") or "")
            if item_id:
                options.append({"id": item_id, "name": str(taxonomy_item.get("name") or item_id)})
    return tuple(options)


def exported_labeling_dataset_name(queue_name: str) -> str:
    """Return the exported labeled dataset name for a queue file."""
    if queue_name.endswith("_labeling_queue.jsonl"):
        return queue_name.removesuffix("_labeling_queue.jsonl") + "_labeled_queue_export.jsonl"
    return Path(queue_name).stem + "_labeled_queue_export.jsonl"


def dataset_list_row(artifact: Any, result: Any) -> dict[str, Any]:
    """Return one datasets table row."""
    summary = result.summary
    return {
        "name": artifact.name,
        "examples": summary.example_count if summary else None,
        "valid": result.valid,
        "categories": len(summary.category_coverage) if summary else None,
        "tags": len(summary.tag_coverage) if summary else None,
        "needs_review": summary.needs_review_counts.get("true", 0) if summary else None,
        "expected_unknown": summary.expected_unknown_count if summary else None,
        "error_lines": result.error_lines,
    }


def dataset_summary_cards(result: Any) -> tuple[dict[str, Any], ...]:
    """Return dataset detail summary cards."""
    summary = result.summary
    return (
        {"label": "Examples", "value": summary.example_count if summary else "n/a"},
        {"label": "Valid", "value": "Yes" if result.valid else "No", "tone": "success" if result.valid else "danger"},
        {"label": "Categories covered", "value": len(summary.category_coverage) if summary else "n/a"},
        {"label": "Tags covered", "value": len(summary.tag_coverage) if summary else "n/a"},
        {"label": "Needs review", "value": summary.needs_review_counts.get("true", 0) if summary else "n/a"},
        {"label": "Expected UNKNOWN", "value": expected_unknown_value(summary)},
    )


def expected_unknown_value(summary: Any) -> int | str:
    """Return the expected UNKNOWN display value for a dataset summary."""
    if summary is None or summary.expected_unknown_count is None:
        return "n/a"
    return summary.expected_unknown_count


def dataset_example_row(example: Mapping[str, Any]) -> dict[str, Any]:
    """Return one read-only dataset example preview row."""
    transaction = mapping_value(example.get("transaction"))
    expected = mapping_value(example.get("expected"))
    taxonomy = mapping_value(example.get("candidate_taxonomy"))
    expected_tag_ids = tuple(str(tag_id) for tag_id in list_value(expected.get("tag_ids")))
    return {
        "request_id": str(example.get("request_id") or ""),
        "description": str(transaction.get("description") or ""),
        "amount": transaction.get("amount"),
        "expected_category": taxonomy_name(taxonomy, "categories", str(expected.get("category_id") or "")),
        "expected_tags": [taxonomy_name(taxonomy, "tags", tag_id) for tag_id in expected_tag_ids],
        "needs_review": bool(expected.get("needs_review")),
        "label_source": str(example.get("label_source") or ""),
    }


def prompt_list_row(prompt: Any, runs: Sequence[Any]) -> dict[str, Any]:
    """Return one prompts table row."""
    return {
        "name": prompt.name,
        "last_modified": format_modified_at(prompt.modified_at),
        "run_count": sum(1 for run in runs if run.prompt_id == prompt.prompt_id),
    }


def normalize_run_form(values: Mapping[str, Any], *, submitted: bool) -> dict[str, Any]:
    """Normalize new-run form values for display and validation."""
    prompt_name = str(values.get("prompt") or "")
    dataset_name = str(values.get("dataset") or "")
    run_name = str(values.get("run_name") or "").strip()
    if not run_name:
        run_name = default_run_name(prompt_name, dataset_name)

    limit_text = str(values.get("limit") or "").strip()
    temperature_text = str(values.get("temperature") or DEFAULT_RUN_TEMPERATURE).strip()
    score_auto = values.get("score_auto") == "on" if submitted else True
    overwrite = values.get("overwrite") == "on"
    form = {
        "prompt": prompt_name,
        "dataset": dataset_name,
        "model": str(values.get("model") or DEFAULT_RUN_MODEL).strip() or DEFAULT_RUN_MODEL,
        "temperature": temperature_text or DEFAULT_RUN_TEMPERATURE,
        "limit": limit_text,
        "limit_value": None,
        "run_name": run_name,
        "score_auto": score_auto,
        "overwrite": overwrite,
    }
    return form


def default_run_name(prompt_name: str, dataset_name: str) -> str:
    """Return a timestamped default run name."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prompt_stem = Path(prompt_name).stem if prompt_name else "prompt"
    dataset_stem = Path(dataset_name).stem if dataset_name else "dataset"
    if prompt_name or dataset_name:
        return f"{dataset_stem}_{prompt_stem}_{timestamp}"
    return f"eval_run_{timestamp}"


def run_preflight(form: dict[str, Any], *, api_key_configured: bool) -> dict[str, Any]:
    """Return new-run preflight status rows and booleans."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.dataset_service import (
        read_dataset_examples,
        resolve_dataset_path,
        validate_dataset_file,
    )
    from evals.llm_categorization.services.prompt_service import resolve_prompt_path
    from evals.llm_categorization.services.run_service import resolve_run_path

    errors: list[str] = []
    prompt_exists = False
    dataset_valid = False
    output_available = False
    examples_to_run: int | None = None

    try:
        if form["prompt"]:
            resolve_prompt_path(str(form["prompt"]))
            prompt_exists = True
    except (FileNotFoundError, ValueError) as exc:
        errors.append(str(exc))

    try:
        if form["dataset"]:
            dataset_path = resolve_dataset_path(str(form["dataset"]))
            validation_result = validate_dataset_file(dataset_path)
            dataset_valid = validation_result.valid
            if not validation_result.valid:
                errors.extend(validation_result.error_lines)
            else:
                total_examples = len(read_dataset_examples(dataset_path))
                examples_to_run = total_examples
    except (FileNotFoundError, ValueError) as exc:
        errors.append(str(exc))

    limit_value = parse_optional_positive_int(str(form.get("limit") or ""), "Limit", errors)
    form["limit_value"] = limit_value
    temperature_value = parse_non_negative_float(str(form.get("temperature") or ""), "Temperature", errors)
    form["temperature_value"] = temperature_value
    if examples_to_run is not None and limit_value is not None:
        examples_to_run = min(examples_to_run, limit_value)

    try:
        run_path = resolve_run_path(str(form["run_name"]), must_exist=False)
        output_available = not run_path.exists() or bool(form.get("overwrite"))
        if run_path.exists() and not form.get("overwrite"):
            errors.append("Run output folder already exists. Confirm overwrite to replace artifacts.")
    except ValueError as exc:
        errors.append(str(exc))

    can_dry_run = prompt_exists and dataset_valid and output_available and temperature_value is not None and not errors
    can_start = can_dry_run and api_key_configured
    return {
        "rows": (
            {"check": "Dataset valid", "status": yes_no(dataset_valid), "ok": dataset_valid},
            {"check": "Prompt exists", "status": yes_no(prompt_exists), "ok": prompt_exists},
            {"check": "API key configured", "status": yes_no(api_key_configured), "ok": api_key_configured},
            {"check": "Output folder available", "status": yes_no(output_available), "ok": output_available},
            {
                "check": "Examples to run",
                "status": examples_to_run if examples_to_run is not None else "n/a",
                "ok": True,
            },
        ),
        "can_dry_run": can_dry_run,
        "can_start": can_start,
        "errors": tuple(errors),
    }


def parse_optional_positive_int(value: str, label: str, errors: list[str]) -> int | None:
    """Parse an optional positive integer form value."""
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        errors.append(f"{label} must be a positive integer.")
        return None
    if parsed <= 0:
        errors.append(f"{label} must be a positive integer.")
        return None
    return parsed


def parse_non_negative_float(value: str, label: str, errors: list[str]) -> float | None:
    """Parse a required non-negative float form value."""
    try:
        parsed = float(value)
    except ValueError:
        errors.append(f"{label} must be non-negative.")
        return None
    if parsed < 0:
        errors.append(f"{label} must be non-negative.")
        return None
    return parsed


def yes_no(value: bool) -> str:
    """Return Yes or No for boolean display."""
    return "Yes" if value else "No"


def run_list_row(run: Any) -> dict[str, Any]:
    """Return one runs table row."""
    metrics = read_json_artifact(run.path / "metrics.json") if run.has_metrics else {}
    config = read_json_artifact(run.path / "config.json") if run.has_config else {}
    headline = mapping_value(metrics.get("headline"))
    comparable = (
        run.has_metrics and (run.path / "failures.jsonl").exists() and (run.path / "scored_outputs.jsonl").exists()
    )
    return {
        "name": run.path.name,
        "run_id": run.run_id,
        "prompt": path_name(config.get("prompt_path")) or run.prompt_id or "n/a",
        "dataset": path_name(config.get("dataset_path")) or "n/a",
        "model": run.model or "n/a",
        "examples": run.example_count if run.example_count is not None else "n/a",
        "category_accuracy": format_percent(headline.get("category_accuracy")),
        "exact_match": format_percent(headline.get("exact_taxonomy_match_rate")),
        "unsafe_auto": format_percent(headline.get("unsafe_auto_assignment_rate")),
        "status": run_status(run, config),
        "comparable": comparable,
    }


def run_status(run: Any, config: Mapping[str, Any]) -> str:
    """Return a concise run status."""
    if run.has_metrics:
        return "Scored"
    if config.get("dry_run") is True:
        return "Dry run"
    if run.has_raw_outputs:
        return "Not scored"
    return "Incomplete"


def run_header(run_name: str, config: Mapping[str, Any], metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return run detail header fields."""
    metric_run = mapping_value(metrics.get("run")) if metrics else {}
    return {
        "name": str(config.get("run_id") or run_name),
        "prompt": path_name(config.get("prompt_path")) or "n/a",
        "dataset": path_name(config.get("dataset_path")) or path_name(metric_run.get("dataset")) or "n/a",
        "model": str(config.get("model") or "n/a"),
        "temperature": config.get("temperature", "n/a"),
        "examples": config.get("number_of_examples", metric_run.get("example_count", "n/a")),
    }


def run_summary_cards(headline: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return run detail summary cards."""
    return (
        {"label": "Valid JSON", "value": format_percent(headline.get("valid_json_rate"))},
        {"label": "Category accuracy", "value": format_percent(headline.get("category_accuracy"))},
        {"label": "Exact match", "value": format_percent(headline.get("exact_taxonomy_match_rate"))},
        {"label": "Tag F1", "value": format_percent(headline.get("tag_micro_f1"))},
        {"label": "Unsafe auto-assignment", "value": format_percent(headline.get("unsafe_auto_assignment_rate"))},
        {"label": "High-confidence wrong", "value": format_percent(headline.get("high_confidence_wrong_rate"))},
    )


def run_metric_rows(headline: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    """Return run detail metrics table rows."""
    return tuple(
        {"label": label, "value": format_percent(headline.get(key))}
        for label, key in (
            ("valid JSON rate", "valid_json_rate"),
            ("schema-valid rate", "schema_valid_rate"),
            ("category accuracy", "category_accuracy"),
            ("known-category accuracy", "known_category_accuracy"),
            ("exact taxonomy match", "exact_taxonomy_match_rate"),
            ("tag micro-F1", "tag_micro_f1"),
            ("UNKNOWN precision", "unknown_precision"),
            ("UNKNOWN recall", "unknown_recall"),
            ("needs-review F1", "needs_review_f1"),
            ("unsafe auto-assignment rate", "unsafe_auto_assignment_rate"),
            ("high-confidence wrong rate", "high_confidence_wrong_rate"),
        )
    )


def dataset_records_for_run(run_path: Path, config: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Return dataset records keyed by request ID for run failure detail."""
    ensure_eval_services_importable()
    from evals.llm_categorization.services.scoring_service import dataset_path_for_run
    from evals.llm_categorization.tools.io_utils import read_jsonl

    dataset_path: Path | None
    try:
        dataset_path = dataset_path_for_run(run_path)
    except ValueError:
        dataset_path_value = config.get("dataset_path")
        dataset_path = Path(str(dataset_path_value)) if dataset_path_value else None
    if dataset_path is None or not dataset_path.exists():
        return {}
    return {str(record.get("request_id")): record for _, record in read_jsonl(dataset_path) if record.get("request_id")}


def failure_row(failure: Mapping[str, Any], dataset_records: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Return one run failure row with inline detail fields."""
    request_id = str(failure.get("request_id") or "")
    expected = mapping_value(failure.get("expected"))
    predicted = mapping_value(failure.get("predicted"))
    record = dataset_records.get(request_id, {})
    transaction = mapping_value(record.get("transaction"))
    return {
        "request_id": request_id,
        "failure_modes": list_value(failure.get("failure_modes")),
        "expected": compact_assignment(expected),
        "predicted": compact_assignment(predicted),
        "confidence": predicted.get("confidence"),
        "review": predicted.get("needs_review"),
        "description": str(transaction.get("description") or ""),
        "amount": transaction.get("amount", ""),
        "expected_category": expected.get("category_name") or expected.get("category_id"),
        "expected_tags": list_value(expected.get("tag_ids")),
        "expected_review": expected.get("needs_review"),
        "predicted_category": predicted.get("category_name") or predicted.get("category_id"),
        "predicted_tags": list_value(predicted.get("tag_ids")),
        "predicted_review": predicted.get("needs_review"),
        "reason": predicted.get("reason") or "",
    }


def compact_assignment(value: Mapping[str, Any]) -> str:
    """Return a compact category/tags assignment display."""
    category = value.get("category_name") or value.get("category_id") or "n/a"
    tags = ", ".join(str(tag_id) for tag_id in list_value(value.get("tag_ids")))
    return f"{category} [{tags}]" if tags else str(category)


def comparison_run_row(run_path: Path) -> dict[str, Any]:
    """Return one compared run row from saved scoring artifacts."""
    config = read_json_artifact(run_path / "config.json")
    metrics = read_json_artifact(run_path / "metrics.json")
    metric_run = mapping_value(metrics.get("run"))
    dataset_hash = str(config.get("dataset_hash") or metric_run.get("dataset_hash") or "")
    headline = mapping_value(metrics.get("headline"))
    return {
        "name": run_path.name,
        "run_id": str(config.get("run_id") or run_path.name),
        "prompt": path_name(config.get("prompt_path")) or "n/a",
        "dataset": path_name(config.get("dataset_path")) or path_name(metric_run.get("dataset")) or "n/a",
        "model": str(config.get("model") or "n/a"),
        "dataset_hash": dataset_hash,
        "dataset_hash_short": dataset_hash[:12] if dataset_hash else "n/a",
        "headline": headline,
    }


def comparison_metric_rows(run_rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Return the fixed Prompt Lab comparison metric table."""
    return tuple(
        {
            "label": label,
            "values": [format_percent(mapping_value(row.get("headline")).get(metric_name)) for row in run_rows],
        }
        for label, metric_name in (
            ("Valid JSON", "valid_json_rate"),
            ("Category accuracy", "category_accuracy"),
            ("Exact match", "exact_taxonomy_match_rate"),
            ("Tag micro-F1", "tag_micro_f1"),
            ("UNKNOWN recall", "unknown_recall"),
            ("Unsafe auto-assignment", "unsafe_auto_assignment_rate"),
            ("High-confidence wrong", "high_confidence_wrong_rate"),
        )
    )


def comparison_interpretation(run_rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, str], ...]:
    """Return a concise deterministic interpretation summary for compared runs."""
    return (
        {
            "label": "Most accurate",
            "value": best_comparison_run(run_rows, "category_accuracy", higher_is_better=True),
        },
        {"label": "Safest", "value": safest_comparison_run(run_rows)},
        {
            "label": "Best tag behavior",
            "value": best_comparison_run(run_rows, "tag_micro_f1", higher_is_better=True),
        },
        {
            "label": "Most unsafe",
            "value": best_comparison_run(run_rows, "unsafe_auto_assignment_rate", higher_is_better=True),
        },
    )


def best_comparison_run(
    run_rows: Sequence[Mapping[str, Any]],
    metric_name: str,
    *,
    higher_is_better: bool,
) -> str:
    """Return the best run label for a single comparison metric."""
    available = []
    for row in run_rows:
        value = metric_float(row, metric_name)
        if value is not None:
            available.append((str(row.get("run_id") or row.get("name") or ""), value))
    if not available:
        return "n/a"
    target = (max if higher_is_better else min)(value for _, value in available)
    winners = sorted(run_id for run_id, value in available if value == target)
    return f"{', '.join(winners)} ({format_percent(target)})"


def safest_comparison_run(run_rows: Sequence[Mapping[str, Any]]) -> str:
    """Return the safest compared run by unsafe and high-confidence-wrong rates."""
    available = []
    for row in run_rows:
        unsafe = metric_float(row, "unsafe_auto_assignment_rate")
        high_wrong = metric_float(row, "high_confidence_wrong_rate")
        if unsafe is None and high_wrong is None:
            continue
        available.append((unsafe or 0.0, high_wrong or 0.0, str(row.get("run_id") or row.get("name") or "")))
    if not available:
        return "n/a"
    target = min((unsafe, high_wrong) for unsafe, high_wrong, _ in available)
    winners = sorted(run_id for unsafe, high_wrong, run_id in available if (unsafe, high_wrong) == target)
    return f"{', '.join(winners)} ({format_percent(target[0])} unsafe)"


def metric_float(run_row: Mapping[str, Any], metric_name: str) -> float | None:
    """Return one headline metric as a float."""
    return numeric_value(mapping_value(run_row.get("headline")).get(metric_name))


def path_name(value: Any) -> str:
    """Return the file or directory name from a path-like value."""
    if not value:
        return ""
    return Path(str(value)).name


def format_percent(value: Any) -> str:
    """Format a fraction metric as a percentage string."""
    number = numeric_value(value)
    if number is None:
        return "n/a"
    return f"{number * 100:.1f}%"


def read_json_artifact(path: Path) -> dict[str, Any]:
    """Read an optional JSON artifact as an object."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def format_modified_at(timestamp: float) -> str:
    """Format an artifact modification timestamp for developer pages."""
    if not timestamp:
        return "n/a"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def model_input_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the model input JSON shown in prompt preview."""
    return {
        "request_id": record.get("request_id"),
        "transaction": record.get("transaction"),
        "candidate_taxonomy": record.get("candidate_taxonomy"),
        "similar_transactions": record.get("similar_transactions"),
    }


def expected_label_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the expected label JSON kept collapsed in prompt preview."""
    return {
        "expected": record.get("expected"),
        "label_source": record.get("label_source"),
        "coverage": record.get("coverage"),
        "notes": record.get("notes"),
    }


def taxonomy_name(taxonomy: Mapping[str, Any], collection_name: str, item_id: str) -> str:
    """Return a taxonomy item display name with ID fallback."""
    for item in list_value(taxonomy.get(collection_name)):
        if not isinstance(item, Mapping):
            continue
        if item.get("id") == item_id:
            name = item.get("name")
            return str(name or item_id)
    return item_id


def mapping_value(value: Any) -> Mapping[str, Any]:
    """Return a mapping value or an empty mapping."""
    return value if isinstance(value, Mapping) else {}


def list_value(value: Any) -> Sequence[Any]:
    """Return a list-like value or an empty tuple."""
    return value if isinstance(value, list) else ()


def numeric_value(value: Any) -> float | None:
    """Return a numeric value as float, excluding booleans."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None
