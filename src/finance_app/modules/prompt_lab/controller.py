"""Flask routes for the local developer Prompt Lab."""

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue

from finance_app.core.i18n import gettext
from finance_app.modules.auth.permissions import owner_required
from finance_app.modules.prompt_lab.service import (
    build_dataset_builder_context,
    build_dataset_detail_context,
    build_datasets_context,
    build_labeling_item_context,
    build_labeling_queue_detail_context,
    build_labeling_queues_context,
    build_new_run_context,
    build_overview_context,
    build_prompt_editor_context,
    build_prompt_preview_context,
    build_prompts_context,
    build_run_comparison_context,
    build_run_detail_context,
    build_runs_context,
    export_labeling_queue_from_form,
    launch_prompt_lab_run,
    mark_labeling_item_unusable_from_form,
    preview_dataset_build_from_form,
    rescore_run_by_name,
    run_dataset_build_from_form,
    save_labeling_item_from_form,
    save_prompt_content,
    save_prompt_copy,
    validate_dataset_by_name,
)

prompt_lab_bp = Blueprint("prompt_lab", __name__)


def ensure_prompt_lab_enabled() -> None:
    """Hide Prompt Lab unless the application is running in development mode."""
    app_settings = current_app.config.get("FINANCE_SETTINGS")
    if not bool(getattr(app_settings, "server_debug", False)):
        abort(404)


@prompt_lab_bp.route("/admin/prompt-lab")
@owner_required
def overview() -> ResponseReturnValue:
    """Render the local Prompt Lab overview without mutating app data."""
    ensure_prompt_lab_enabled()
    return render_template("prompt_lab_overview.html", **build_overview_context())


@prompt_lab_bp.route("/admin/prompt-lab/datasets")
@owner_required
def datasets() -> ResponseReturnValue:
    """Render the local Prompt Lab datasets list."""
    ensure_prompt_lab_enabled()
    return render_template("prompt_lab_datasets.html", **build_datasets_context())


@prompt_lab_bp.route("/admin/prompt-lab/datasets/build")
@owner_required
def dataset_build() -> ResponseReturnValue:
    """Render the coverage-driven dataset build form."""
    ensure_prompt_lab_enabled()
    app_settings = current_app.config["FINANCE_SETTINGS"]
    return render_template(
        "prompt_lab_dataset_build.html",
        **build_dataset_builder_context(request.args, default_db_path=app_settings.database_path),
    )


@prompt_lab_bp.route("/admin/prompt-lab/datasets/build/preview", methods=["POST"])
@owner_required
def dataset_build_preview() -> ResponseReturnValue:
    """Preview coverage for a submitted dataset specification."""
    ensure_prompt_lab_enabled()
    app_settings = current_app.config["FINANCE_SETTINGS"]
    try:
        preview = preview_dataset_build_from_form(request.form, default_db_path=app_settings.database_path)
    except (OSError, ValueError) as exc:
        context = build_dataset_builder_context(
            request.form,
            default_db_path=app_settings.database_path,
            errors=[str(exc)],
        )
    else:
        context = build_dataset_builder_context(
            request.form,
            default_db_path=app_settings.database_path,
            preview=preview,
        )
    return render_template("prompt_lab_dataset_build.html", **context)


@prompt_lab_bp.route("/admin/prompt-lab/datasets/build/run", methods=["POST"])
@owner_required
def dataset_build_run() -> ResponseReturnValue:
    """Build draft dataset artifacts from a submitted dataset specification."""
    ensure_prompt_lab_enabled()
    app_settings = current_app.config["FINANCE_SETTINGS"]
    try:
        result = run_dataset_build_from_form(request.form, default_db_path=app_settings.database_path)
    except (OSError, ValueError) as exc:
        context = build_dataset_builder_context(
            request.form,
            default_db_path=app_settings.database_path,
            errors=[str(exc)],
        )
    else:
        flash(gettext("Draft dataset built."))
        context = build_dataset_builder_context(
            request.form,
            default_db_path=app_settings.database_path,
            build_result=result,
        )
    return render_template("prompt_lab_dataset_build.html", **context)


@prompt_lab_bp.route("/admin/prompt-lab/datasets/<dataset_name>")
@owner_required
def dataset_detail(dataset_name: str) -> ResponseReturnValue:
    """Render a read-only Prompt Lab dataset detail page."""
    ensure_prompt_lab_enabled()
    try:
        context = build_dataset_detail_context(dataset_name)
    except (FileNotFoundError, ValueError):
        abort(404)
    return render_template("prompt_lab_dataset_detail.html", **context)


@prompt_lab_bp.route("/admin/prompt-lab/labeling")
@owner_required
def labeling_queues() -> ResponseReturnValue:
    """Render available AI-problem manual labeling queues."""
    ensure_prompt_lab_enabled()
    return render_template("prompt_lab_labeling_queues.html", **build_labeling_queues_context())


@prompt_lab_bp.route("/admin/prompt-lab/labeling/<queue_name>")
@owner_required
def labeling_queue_detail(queue_name: str) -> ResponseReturnValue:
    """Render one AI-problem labeling queue."""
    ensure_prompt_lab_enabled()
    try:
        context = build_labeling_queue_detail_context(queue_name, request.args)
    except (FileNotFoundError, ValueError):
        abort(404)
    return render_template("prompt_lab_labeling_queue_detail.html", **context)


@prompt_lab_bp.route("/admin/prompt-lab/labeling/<queue_name>/<request_id>")
@owner_required
def labeling_item(queue_name: str, request_id: str) -> ResponseReturnValue:
    """Render one labeling queue item and manual label form."""
    ensure_prompt_lab_enabled()
    try:
        context = build_labeling_item_context(queue_name, request_id)
    except (FileNotFoundError, ValueError):
        abort(404)
    return render_template("prompt_lab_labeling_item.html", **context)


@prompt_lab_bp.route("/admin/prompt-lab/labeling/<queue_name>/<request_id>/save", methods=["POST"])
@owner_required
def save_labeling_item(queue_name: str, request_id: str) -> ResponseReturnValue:
    """Save a manual label for one queue item."""
    ensure_prompt_lab_enabled()
    try:
        next_request_id = save_labeling_item_from_form(queue_name, request_id, request.form)
    except (FileNotFoundError, ValueError) as exc:
        try:
            context = build_labeling_item_context(queue_name, request_id, values=request.form, errors=[str(exc)])
        except (FileNotFoundError, ValueError):
            abort(404)
        return render_template("prompt_lab_labeling_item.html", **context)
    flash(gettext("Manual label saved."))
    if request.form.get("save_action") == "save_next" and next_request_id:
        return redirect(url_for("prompt_lab.labeling_item", queue_name=queue_name, request_id=next_request_id))
    return redirect(url_for("prompt_lab.labeling_item", queue_name=queue_name, request_id=request_id))


@prompt_lab_bp.route("/admin/prompt-lab/labeling/<queue_name>/<request_id>/mark-unusable", methods=["POST"])
@owner_required
def mark_labeling_item_unusable(queue_name: str, request_id: str) -> ResponseReturnValue:
    """Mark one queue item unusable."""
    ensure_prompt_lab_enabled()
    try:
        mark_labeling_item_unusable_from_form(queue_name, request_id, request.form)
    except (FileNotFoundError, ValueError) as exc:
        flash(gettext("Queue item could not be updated: {error}", error=str(exc)))
    else:
        flash(gettext("Queue item marked unusable."))
    return redirect(url_for("prompt_lab.labeling_queue_detail", queue_name=queue_name))


@prompt_lab_bp.route("/admin/prompt-lab/labeling/<queue_name>/export", methods=["POST"])
@owner_required
def export_labeling_queue(queue_name: str) -> ResponseReturnValue:
    """Export labeled queue items to a valid evaluation JSONL dataset."""
    ensure_prompt_lab_enabled()
    try:
        dataset_name = export_labeling_queue_from_form(queue_name)
    except (FileNotFoundError, OSError, ValueError) as exc:
        flash(gettext("Labeling queue could not be exported: {error}", error=str(exc)))
        return redirect(url_for("prompt_lab.labeling_queue_detail", queue_name=queue_name))
    flash(gettext("Labeled queue exported."))
    return redirect(url_for("prompt_lab.dataset_detail", dataset_name=dataset_name))


@prompt_lab_bp.route("/admin/prompt-lab/datasets/<dataset_name>/validate", methods=["POST"])
@owner_required
def validate_dataset(dataset_name: str) -> ResponseReturnValue:
    """Validate a local Prompt Lab dataset and render the read-only detail page."""
    ensure_prompt_lab_enabled()
    try:
        validation_result = validate_dataset_by_name(dataset_name)
        context = build_dataset_detail_context(
            dataset_name,
            validation_result=validation_result,
            validation_completed=True,
        )
    except (FileNotFoundError, ValueError):
        abort(404)
    return render_template("prompt_lab_dataset_detail.html", **context)


@prompt_lab_bp.route("/admin/prompt-lab/prompts")
@owner_required
def prompts() -> ResponseReturnValue:
    """Render the local Prompt Lab prompts list."""
    ensure_prompt_lab_enabled()
    return render_template("prompt_lab_prompts.html", **build_prompts_context())


@prompt_lab_bp.route("/admin/prompt-lab/prompts/preview", methods=["GET", "POST"])
@owner_required
def prompt_preview() -> ResponseReturnValue:
    """Render a prompt preview without calling a model provider."""
    ensure_prompt_lab_enabled()
    values = request.form if request.method == "POST" else request.args
    context = build_prompt_preview_context(
        selected_prompt=str(values.get("prompt") or ""),
        selected_dataset=str(values.get("dataset") or ""),
        selected_request_id=str(values.get("request_id") or ""),
        render=request.method == "POST",
    )
    return render_template("prompt_lab_prompt_preview.html", **context)


@prompt_lab_bp.route("/admin/prompt-lab/prompts/save-as", methods=["POST"])
@owner_required
def save_prompt_as() -> ResponseReturnValue:
    """Save prompt editor content as a new prompt candidate."""
    ensure_prompt_lab_enabled()
    source_prompt_name = str(request.form.get("source_prompt_name") or "")
    prompt_content = str(request.form.get("prompt_content") or "")
    new_prompt_name = str(request.form.get("new_prompt_name") or "").strip()
    overwrite = request.form.get("overwrite_confirm") == "on"

    try:
        saved_name = save_prompt_copy(new_prompt_name, prompt_content, overwrite=overwrite)
    except FileExistsError:
        flash(gettext("Prompt already exists. Confirm overwrite to replace it."))
        return render_template(
            "prompt_lab_prompt_editor.html",
            **build_prompt_editor_context(
                source_prompt_name,
                prompt_content=prompt_content,
                new_prompt_name=new_prompt_name,
                show_overwrite_warning=True,
            ),
        )
    except ValueError as exc:
        flash(gettext(str(exc)))
        return render_template(
            "prompt_lab_prompt_editor.html",
            **build_prompt_editor_context(
                source_prompt_name,
                prompt_content=prompt_content,
                new_prompt_name=new_prompt_name,
            ),
        )
    except FileNotFoundError:
        abort(404)

    flash(gettext("Prompt saved as {prompt_name}.", prompt_name=saved_name))
    return redirect(url_for("prompt_lab.prompt_editor", prompt_name=saved_name))


@prompt_lab_bp.route("/admin/prompt-lab/prompts/<prompt_name>")
@owner_required
def prompt_editor(prompt_name: str) -> ResponseReturnValue:
    """Render a plain-text prompt editor for one prompt candidate."""
    ensure_prompt_lab_enabled()
    try:
        context = build_prompt_editor_context(prompt_name)
    except (FileNotFoundError, ValueError):
        abort(404)
    return render_template("prompt_lab_prompt_editor.html", **context)


@prompt_lab_bp.route("/admin/prompt-lab/prompts/<prompt_name>/save", methods=["POST"])
@owner_required
def save_prompt(prompt_name: str) -> ResponseReturnValue:
    """Save prompt editor content to an existing prompt candidate."""
    ensure_prompt_lab_enabled()
    prompt_content = str(request.form.get("prompt_content") or "")
    try:
        save_prompt_content(prompt_name, prompt_content)
    except (FileNotFoundError, ValueError):
        abort(404)
    flash(gettext("Prompt saved."))
    return redirect(url_for("prompt_lab.prompt_editor", prompt_name=prompt_name))


@prompt_lab_bp.route("/admin/prompt-lab/runs")
@owner_required
def runs() -> ResponseReturnValue:
    """Render the local Prompt Lab runs list."""
    ensure_prompt_lab_enabled()
    return render_template("prompt_lab_runs.html", **build_runs_context())


@prompt_lab_bp.route("/admin/prompt-lab/runs/compare", methods=["POST"])
@owner_required
def compare_runs() -> ResponseReturnValue:
    """Compare selected scored Prompt Lab runs without calling a model provider."""
    ensure_prompt_lab_enabled()
    selected_runs = request.form.getlist("run_names")
    if len(selected_runs) < 2:
        flash(gettext("Choose at least two scored runs to compare."))
        return redirect(url_for("prompt_lab.runs"))
    try:
        context = build_run_comparison_context(selected_runs)
    except (OSError, ValueError) as exc:
        flash(gettext("Run comparison failed: {error}", error=str(exc)))
        return redirect(url_for("prompt_lab.runs"))
    return render_template("prompt_lab_run_compare.html", **context)


@prompt_lab_bp.route("/admin/prompt-lab/runs/new", methods=["GET", "POST"])
@owner_required
def new_run() -> ResponseReturnValue:
    """Render or submit the synchronous Prompt Lab eval-run form."""
    ensure_prompt_lab_enabled()
    app_settings = current_app.config["FINANCE_SETTINGS"]
    api_key = str(getattr(app_settings, "openai_api_key", "") or "")
    values = request.form if request.method == "POST" else request.args
    submitted = request.method == "POST"
    context = build_new_run_context(values, submitted=submitted, api_key_configured=bool(api_key))

    if request.method == "GET":
        return render_template("prompt_lab_run_new.html", **context)

    action = request.form.get("run_action")
    preflight = context["preflight"]
    if action not in {"dry_run", "start"}:
        context = build_new_run_context(
            request.form,
            submitted=True,
            api_key_configured=bool(api_key),
            errors=["Choose Dry run or Start run."],
        )
        return render_template("prompt_lab_run_new.html", **context)
    if action == "start" and not preflight["can_start"]:
        errors = ["Start run is blocked by preflight checks."]
        if not api_key:
            errors.append("API key is missing.")
        context = build_new_run_context(
            request.form,
            submitted=True,
            api_key_configured=bool(api_key),
            errors=errors,
        )
        return render_template("prompt_lab_run_new.html", **context)
    if action == "dry_run" and not preflight["can_dry_run"]:
        context = build_new_run_context(
            request.form,
            submitted=True,
            api_key_configured=bool(api_key),
            errors=["Dry run is blocked by preflight checks."],
        )
        return render_template("prompt_lab_run_new.html", **context)

    try:
        run_name = launch_prompt_lab_run(
            context["form"],
            dry_run=(action == "dry_run"),
            api_key=api_key,
            config_path=app_settings.config_path,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        context = build_new_run_context(
            request.form,
            submitted=True,
            api_key_configured=bool(api_key),
            errors=[str(exc)],
        )
        return render_template("prompt_lab_run_new.html", **context)

    flash(gettext("Dry run completed." if action == "dry_run" else "Run started."))
    return redirect(url_for("prompt_lab.run_detail", run_name=run_name))


@prompt_lab_bp.route("/admin/prompt-lab/runs/<run_name>")
@owner_required
def run_detail(run_name: str) -> ResponseReturnValue:
    """Render Prompt Lab run metrics and failures."""
    ensure_prompt_lab_enabled()
    try:
        context = build_run_detail_context(run_name)
    except (FileNotFoundError, ValueError):
        abort(404)
    return render_template("prompt_lab_run_detail.html", **context)


@prompt_lab_bp.route("/admin/prompt-lab/runs/<run_name>/rescore", methods=["POST"])
@owner_required
def rescore_run(run_name: str) -> ResponseReturnValue:
    """Re-score saved raw outputs without calling a model provider."""
    ensure_prompt_lab_enabled()
    try:
        rescore_run_by_name(run_name)
    except (FileNotFoundError, OSError, ValueError) as exc:
        flash(gettext("Run could not be re-scored: {error}", error=str(exc)))
    else:
        flash(gettext("Run re-scored."))
    return redirect(url_for("prompt_lab.run_detail", run_name=run_name))
