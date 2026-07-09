"""Prompt services for local LLM categorization eval artifacts.

The functions list prompt candidates, read and write prompt Markdown files, and
render deterministic prompt previews without calling a model provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.llm_categorization.services import PROMPTS_DIR, file_modified_at, resolve_under_root
from evals.llm_categorization.tools import render_prompt
from evals.llm_categorization.tools.summarize_dataset import load_validated_records


@dataclass(frozen=True)
class PromptArtifact:
    """Represent one prompt Markdown artifact."""

    path: Path
    name: str
    prompt_id: str
    size_bytes: int
    modified_at: float


def list_prompt_files(prompts_dir: Path | None = None) -> tuple[PromptArtifact, ...]:
    """List prompt Markdown files under the eval prompts directory."""
    prompts_dir = prompts_dir or PROMPTS_DIR
    if not prompts_dir.exists():
        return ()
    artifacts = []
    for path in sorted(prompts_dir.glob("*.md"), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue
        artifacts.append(
            PromptArtifact(
                path=path,
                name=path.name,
                prompt_id=path.stem,
                size_bytes=path.stat().st_size,
                modified_at=file_modified_at(path),
            )
        )
    return tuple(artifacts)


def read_prompt_file(path: Path) -> str:
    """Read a prompt candidate Markdown file."""
    return render_prompt.load_prompt(path)


def resolve_prompt_path(prompt_name: str, prompts_dir: Path | None = None) -> Path:
    """Resolve one top-level prompt name safely under the prompts directory."""
    prompts_dir = prompts_dir or PROMPTS_DIR
    if Path(prompt_name).name != prompt_name:
        raise ValueError("prompt name must not contain path separators")
    if Path(prompt_name).suffix.lower() != ".md":
        raise ValueError("prompt name must end with .md")
    prompt_path = resolve_under_root(prompt_name, prompts_dir)
    if not prompt_path.is_file():
        raise FileNotFoundError(prompt_name)
    return prompt_path


def read_prompt_by_name(prompt_name: str, *, prompts_dir: Path | None = None) -> str:
    """Read a prompt candidate by safe prompt file name."""
    return read_prompt_file(resolve_prompt_path(prompt_name, prompts_dir))


def save_prompt_by_name(prompt_name: str, content: str, *, prompts_dir: Path | None = None) -> Path:
    """Save content to an existing prompt candidate file."""
    prompt_path = resolve_prompt_path(prompt_name, prompts_dir)
    prompt_path.write_text(content, encoding="utf-8", newline="\n")
    return prompt_path


def save_prompt_as(
    prompt_name: str,
    content: str,
    *,
    prompts_dir: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Save content as a prompt candidate, requiring explicit overwrite approval."""
    prompts_dir = prompts_dir or PROMPTS_DIR
    if Path(prompt_name).name != prompt_name:
        raise ValueError("prompt name must not contain path separators")
    if Path(prompt_name).suffix.lower() != ".md":
        raise ValueError("prompt name must end with .md")
    prompt_path = resolve_under_root(prompt_name, prompts_dir)
    if prompt_path.exists() and not overwrite:
        raise FileExistsError(prompt_name)
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(content, encoding="utf-8", newline="\n")
    return prompt_path


def write_prompt_file(path: str | Path, content: str, *, prompts_dir: Path | None = None) -> Path:
    """Write prompt Markdown content under the configured prompts directory."""
    prompts_dir = prompts_dir or PROMPTS_DIR
    requested_path = Path(path)
    if requested_path.suffix.lower() != ".md":
        requested_path = requested_path.with_suffix(".md")
    prompt_path = resolve_under_root(requested_path, prompts_dir)
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(content, encoding="utf-8", newline="\n")
    return prompt_path


def render_prompt_preview(
    *,
    prompt_path: Path,
    dataset_path: Path,
    request_id: str | None = None,
    dry_run_count: int | None = None,
) -> dict[str, Any]:
    """Render the exact prompt payload for one request or the first N examples."""
    if request_id is not None and dry_run_count is not None:
        raise ValueError("choose either request_id or dry_run_count, not both")
    prompt_markdown = read_prompt_file(prompt_path)
    _, examples = load_validated_records(dataset_path)
    selected_examples = render_prompt.select_examples(
        examples,
        request_id=request_id,
        dry_run_count=dry_run_count if dry_run_count is not None else 1,
    )
    return render_prompt.render_output_document(
        prompt_path=prompt_path,
        dataset_path=dataset_path,
        prompt_markdown=prompt_markdown,
        examples=selected_examples,
    )
