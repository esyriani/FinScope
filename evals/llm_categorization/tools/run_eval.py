"""Run prompt candidates against eval datasets and save raw LLM outputs.

The runner is the only eval tool that can call a model provider. It uses the
offline prompt renderer for each example, records reproducibility metadata, and
optionally invokes the saved-output scorer after completion. It never opens or
writes FinScope runtime databases.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.llm_categorization.tools import render_prompt, score_outputs
from evals.llm_categorization.tools.io_utils import JsonlError, read_jsonl, write_jsonl
from evals.llm_categorization.tools.schemas import DatasetValidationError, EvaluationExample
from evals.llm_categorization.tools.summarize_dataset import load_validated_records

DEFAULT_TEMPERATURE = 0.0
DEFAULT_RESPONSE_FORMAT = "json_object"
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY_SECONDS = 1.0
API_KEY_ENV_VAR = "OPENAI_API_KEY"


@dataclass(frozen=True)
class RetryPolicy:
    """Represent provider retry behavior for one eval run."""

    max_retries: int
    retry_delay_seconds: float


@dataclass(frozen=True)
class EvalConfig:
    """Represent a reproducible eval-run configuration."""

    prompt_path: Path
    dataset_path: Path
    model: str
    temperature: float
    out_dir: Path
    max_output_tokens: int | None
    response_format: str | None
    limit: int | None
    request_id: str | None
    resume: bool
    dry_run: bool
    score: bool
    retry_policy: RetryPolicy
    config_path: Path
    timeout_seconds: int


@dataclass(frozen=True)
class ProviderResult:
    """Represent a model-provider response and optional token usage."""

    raw_output: str
    token_usage: dict[str, int] | None


class OpenAIChatClient:
    """Small isolated OpenAI chat-completions wrapper for eval runs."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: int,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        """Create an OpenAI-compatible client without importing FinScope runtime modules."""
        if client_factory is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("OpenAI package is not installed.") from exc
            client_factory = OpenAI
        self.client = client_factory(api_key=api_key, timeout=timeout_seconds)

    def complete(
        self,
        *,
        messages: Sequence[Mapping[str, str]],
        model: str,
        temperature: float,
        response_format: str | None,
        max_output_tokens: int | None,
    ) -> ProviderResult:
        """Call the chat-completions API and return raw message text."""
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": list(messages),
        }
        if response_format == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        if max_output_tokens is not None:
            kwargs["max_completion_tokens"] = max_output_tokens

        response = self.client.chat.completions.create(**kwargs)
        raw_output = extract_response_content(response)
        return ProviderResult(raw_output=raw_output, token_usage=extract_token_usage(response))


def extract_response_content(response: Any) -> str:
    """Extract assistant text from an OpenAI-compatible chat response."""
    choices = value_at(response, "choices")
    if not choices:
        raise RuntimeError("OpenAI response did not include choices.")
    first_choice = choices[0]
    message = value_at(first_choice, "message")
    content = value_at(message, "content")
    if not isinstance(content, str):
        raise RuntimeError("OpenAI response message content was not a string.")
    return content


def extract_token_usage(response: Any) -> dict[str, int] | None:
    """Extract token usage from an OpenAI-compatible response when available."""
    usage = value_at(response, "usage")
    if usage is None:
        return None
    token_usage = {}
    for source_name, target_name in (
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("total_tokens", "total_tokens"),
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
    ):
        value = value_at(usage, source_name)
        if isinstance(value, int):
            token_usage[target_name] = value
    return token_usage or None


def value_at(value: Any, key: str) -> Any:
    """Return an attribute or mapping value from provider-shaped objects."""
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def run_evaluation(
    config: EvalConfig,
    *,
    client_factory: Callable[..., Any] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Run an eval configuration and write all configured artifacts."""
    records, examples = load_validated_records(config.dataset_path)
    selected_examples = select_examples(examples, request_id=config.request_id, limit=config.limit)
    selected_records = selected_records_for_examples(records, examples, selected_examples)
    prompt_markdown = render_prompt.load_prompt(config.prompt_path)
    prompt_hash = file_sha256(config.prompt_path)
    dataset_hash = file_sha256(config.dataset_path)
    timestamp = utc_timestamp()
    run_id = config.out_dir.name

    config.out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config.prompt_path, config.out_dir / "prompt.md")
    dataset_meta = build_dataset_meta(config.dataset_path, dataset_hash, examples, selected_examples)
    (config.out_dir / "dataset.meta.json").write_text(
        f"{json.dumps(dataset_meta, ensure_ascii=True, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
        newline="\n",
    )

    completed_request_ids = (
        completed_raw_output_request_ids(config.out_dir / "raw_outputs.jsonl") if config.resume else set()
    )
    pending_examples = [example for example in selected_examples if example.request_id not in completed_request_ids]
    rendered_prompt_records = [
        {
            "request_id": example.request_id,
            "message_payload": render_prompt.render_message_payload(prompt_markdown, example),
        }
        for example in pending_examples
    ]
    if config.dry_run:
        write_jsonl(config.out_dir / "rendered_prompts.jsonl", rendered_prompt_records)
        if not (config.out_dir / "raw_outputs.jsonl").exists() or not config.resume:
            write_jsonl(config.out_dir / "raw_outputs.jsonl", [])
    else:
        effective_api_key = api_key if api_key is not None else load_openai_api_key(config.config_path)
        if not effective_api_key:
            raise RuntimeError(f"OpenAI API key is not configured. Set {API_KEY_ENV_VAR} or config.ini [api_keys].")
        client = OpenAIChatClient(
            api_key=effective_api_key,
            timeout_seconds=config.timeout_seconds,
            client_factory=client_factory,
        )
        append_raw_outputs(
            config,
            prompt_id=config.prompt_path.stem,
            prompt_hash=prompt_hash,
            dataset_hash=dataset_hash,
            timestamp=timestamp,
            client=client,
            examples=pending_examples,
            prompt_markdown=prompt_markdown,
        )

    config_payload = build_config_payload(
        config,
        run_id=run_id,
        prompt_hash=prompt_hash,
        dataset_hash=dataset_hash,
        timestamp=timestamp,
        selected_examples=selected_examples,
        pending_examples=pending_examples,
        completed_request_ids=completed_request_ids,
        scorer_version=score_outputs.SCORER_VERSION if config.score and not config.dry_run else None,
    )
    (config.out_dir / "config.json").write_text(
        f"{json.dumps(config_payload, ensure_ascii=True, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
        newline="\n",
    )

    if config.score and not config.dry_run:
        scoring_dataset_path = scoring_dataset_for_run(config, records, selected_records)
        score_run = score_outputs.score_run(scoring_dataset_path, config.out_dir / "raw_outputs.jsonl")
        score_outputs.write_score_artifacts(score_run, config.out_dir)
    return config_payload


def selected_records_for_examples(
    records: Sequence[dict[str, Any]],
    examples: Sequence[EvaluationExample],
    selected_examples: Sequence[EvaluationExample],
) -> tuple[dict[str, Any], ...]:
    """Return raw dataset records matching selected examples in selected order."""
    records_by_request_id = {example.request_id: record for record, example in zip(records, examples)}
    return tuple(records_by_request_id[example.request_id] for example in selected_examples)


def scoring_dataset_for_run(
    config: EvalConfig,
    records: Sequence[dict[str, Any]],
    selected_records: Sequence[dict[str, Any]],
) -> Path:
    """Return the dataset path to use for automatic scoring."""
    if len(selected_records) == len(records):
        return config.dataset_path
    selected_dataset_path = config.out_dir / "dataset.selected.jsonl"
    write_jsonl(selected_dataset_path, selected_records)
    return selected_dataset_path


def select_examples(
    examples: Sequence[EvaluationExample],
    *,
    request_id: str | None,
    limit: int | None,
) -> tuple[EvaluationExample, ...]:
    """Select examples by request ID or deterministic dataset order."""
    if request_id is not None:
        return (render_prompt.find_example_by_request_id(examples, request_id),)
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be positive")
        return tuple(examples[:limit])
    return tuple(examples)


def append_raw_outputs(
    config: EvalConfig,
    *,
    prompt_id: str,
    prompt_hash: str,
    dataset_hash: str,
    timestamp: str,
    client: OpenAIChatClient,
    examples: Sequence[EvaluationExample],
    prompt_markdown: str,
) -> None:
    """Append raw model outputs for pending examples."""
    raw_outputs_path = config.out_dir / "raw_outputs.jsonl"
    mode = "a" if config.resume and raw_outputs_path.exists() else "w"
    if mode == "a":
        ensure_trailing_newline(raw_outputs_path)
    with raw_outputs_path.open(mode, encoding="utf-8", newline="\n") as output_file:
        for example in examples:
            ensure_expected_label_excluded(example)
            message_payload = render_prompt.render_message_payload(prompt_markdown, example)
            started = time.perf_counter()
            result, attempt_count = call_with_retries(
                client,
                messages=message_payload["messages"],
                model=config.model,
                temperature=config.temperature,
                response_format=config.response_format,
                max_output_tokens=config.max_output_tokens,
                retry_policy=config.retry_policy,
            )
            duration_ms = int(round((time.perf_counter() - started) * 1000))
            row = {
                "request_id": example.request_id,
                "raw_output": result.raw_output,
                "model": config.model,
                "prompt_id": prompt_id,
                "prompt_path": str(config.prompt_path),
                "prompt_hash": prompt_hash,
                "dataset_hash": dataset_hash,
                "timestamp": utc_timestamp(),
                "token_usage": result.token_usage,
                "duration_ms": duration_ms,
                "attempt_count": attempt_count,
                "response_format": config.response_format,
            }
            output_file.write(json.dumps(row, ensure_ascii=True, sort_keys=True))
            output_file.write("\n")
            output_file.flush()


def ensure_trailing_newline(path: Path) -> None:
    """Ensure an existing JSONL file ends with a newline before appending."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("rb+") as file:
        file.seek(-1, os.SEEK_END)
        if file.read(1) != b"\n":
            file.write(b"\n")


def call_with_retries(
    client: OpenAIChatClient,
    *,
    messages: Sequence[Mapping[str, str]],
    model: str,
    temperature: float,
    response_format: str | None,
    max_output_tokens: int | None,
    retry_policy: RetryPolicy,
) -> tuple[ProviderResult, int]:
    """Call the provider with simple bounded retry behavior."""
    attempts = retry_policy.max_retries + 1
    last_error: BaseException | None = None
    for attempt_index in range(attempts):
        try:
            return (
                client.complete(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    response_format=response_format,
                    max_output_tokens=max_output_tokens,
                ),
                attempt_index + 1,
            )
        except Exception as exc:
            last_error = exc
            if attempt_index >= retry_policy.max_retries:
                break
            time.sleep(retry_policy.retry_delay_seconds * (2**attempt_index))
    assert last_error is not None
    raise RuntimeError(f"LLM request failed after {attempts} attempt(s): {type(last_error).__name__}: {last_error}")


def ensure_expected_label_excluded(example: EvaluationExample) -> None:
    """Verify renderer input excludes labels and curation-only metadata."""
    model_input = render_prompt.render_model_input(example)
    forbidden_fields = {"expected", "coverage", "label_source", "privacy_level", "notes"}
    included_fields = sorted(forbidden_fields & set(model_input))
    if included_fields:
        raise RuntimeError(f"rendered model input included forbidden dataset fields: {', '.join(included_fields)}")


def completed_raw_output_request_ids(path: Path) -> set[str]:
    """Return request IDs already present in a raw-output JSONL file."""
    if not path.exists():
        return set()
    completed = set()
    for _, payload in read_jsonl(path):
        request_id = payload.get("request_id")
        raw_output = payload.get("raw_output")
        if isinstance(request_id, str) and isinstance(raw_output, str) and raw_output:
            completed.add(request_id)
    return completed


def build_config_payload(
    config: EvalConfig,
    *,
    run_id: str,
    prompt_hash: str,
    dataset_hash: str,
    timestamp: str,
    selected_examples: Sequence[EvaluationExample],
    pending_examples: Sequence[EvaluationExample],
    completed_request_ids: set[str],
    scorer_version: str | None,
) -> dict[str, Any]:
    """Build deterministic run configuration metadata."""
    return {
        "run_id": run_id,
        "prompt_path": str(config.prompt_path),
        "prompt_hash": prompt_hash,
        "dataset_path": str(config.dataset_path),
        "dataset_hash": dataset_hash,
        "model": config.model,
        "temperature": config.temperature,
        "max_output_tokens": config.max_output_tokens,
        "response_format": config.response_format,
        "git_commit": git_commit_hash(),
        "timestamp": timestamp,
        "number_of_examples": len(selected_examples),
        "completed_before_resume": len(completed_request_ids),
        "examples_run": len(pending_examples),
        "dry_run": config.dry_run,
        "resume": config.resume,
        "retry_policy": {
            "max_retries": config.retry_policy.max_retries,
            "retry_delay_seconds": config.retry_policy.retry_delay_seconds,
        },
        "scorer_version": scorer_version,
    }


def build_dataset_meta(
    dataset_path: Path,
    dataset_hash: str,
    all_examples: Sequence[EvaluationExample],
    selected_examples: Sequence[EvaluationExample],
) -> dict[str, Any]:
    """Build dataset metadata for reproducibility."""
    return {
        "dataset_path": str(dataset_path),
        "dataset_hash": dataset_hash,
        "example_count": len(all_examples),
        "selected_count": len(selected_examples),
        "selected_request_ids": [example.request_id for example in selected_examples],
    }


def load_openai_api_key(config_path: Path) -> str:
    """Load the OpenAI API key using the project convention."""
    env_value = os.environ.get(API_KEY_ENV_VAR, "")
    if env_value:
        return env_value
    if not config_path.exists():
        return ""
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    return parser.get("api_keys", "openai_api_key", fallback="")


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hash of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit_hash() -> str | None:
    """Return the current Git commit hash when Git is available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    commit_hash = result.stdout.strip()
    return commit_hash or None


def utc_timestamp() -> str:
    """Return a compact UTC timestamp for run metadata."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_response_format(value: str) -> str | None:
    """Parse response-format CLI value."""
    if value == "none":
        return None
    return value


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description="Run one LLM categorization prompt candidate on a dataset.")
    parser.add_argument("--prompt", required=True, type=Path, help="Prompt candidate Markdown file.")
    parser.add_argument("--dataset", required=True, type=Path, help="Validated JSONL dataset.")
    parser.add_argument("--model", required=True, help="OpenAI model ID.")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE, help="Sampling temperature.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Run output directory.")
    parser.add_argument("--max-output-tokens", type=int, help="Optional max completion tokens.")
    parser.add_argument(
        "--response-format",
        choices=("json_object", "none"),
        default=DEFAULT_RESPONSE_FORMAT,
        help="Structured JSON response format when supported.",
    )
    parser.add_argument("--limit", type=int, help="Run only the first N examples for a smoke test.")
    parser.add_argument("--request-id", help="Run one dataset example by request_id.")
    parser.add_argument("--resume", action="store_true", help="Skip request IDs already present in raw_outputs.jsonl.")
    parser.add_argument("--dry-run", action="store_true", help="Render prompts without calling the API.")
    parser.add_argument("--no-score", action="store_true", help="Do not automatically run the scoring engine.")
    parser.add_argument(
        "--config", type=Path, default=Path("config.ini"), help="Config file for OpenAI API key fallback."
    )
    parser.add_argument(
        "--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="Provider request timeout."
    )
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="Provider retry count.")
    parser.add_argument(
        "--retry-delay-seconds",
        type=float,
        default=DEFAULT_RETRY_DELAY_SECONDS,
        help="Initial retry delay; later retries use exponential backoff.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> EvalConfig:
    """Build a validated eval config from parsed CLI arguments."""
    if args.temperature < 0:
        raise ValueError("--temperature must be non-negative")
    if args.max_output_tokens is not None and args.max_output_tokens <= 0:
        raise ValueError("--max-output-tokens must be positive")
    if args.limit is not None and args.limit <= 0:
        raise ValueError("--limit must be positive")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be positive")
    if args.max_retries < 0:
        raise ValueError("--max-retries must be zero or greater")
    if args.retry_delay_seconds < 0:
        raise ValueError("--retry-delay-seconds must be zero or greater")
    return EvalConfig(
        prompt_path=args.prompt,
        dataset_path=args.dataset,
        model=args.model,
        temperature=args.temperature,
        out_dir=args.out_dir,
        max_output_tokens=args.max_output_tokens,
        response_format=parse_response_format(args.response_format),
        limit=args.limit,
        request_id=args.request_id,
        resume=args.resume,
        dry_run=args.dry_run,
        score=not args.no_score,
        retry_policy=RetryPolicy(max_retries=args.max_retries, retry_delay_seconds=args.retry_delay_seconds),
        config_path=args.config,
        timeout_seconds=args.timeout_seconds,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the eval-runner CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = config_from_args(args)
        config_payload = run_evaluation(config)
    except (DatasetValidationError, JsonlError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    mode = "dry-run" if config_payload["dry_run"] else "run"
    print(f"Wrote {mode} artifacts for {config_payload['number_of_examples']} example(s) " f"to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
