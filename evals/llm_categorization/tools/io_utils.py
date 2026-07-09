"""JSONL input and output helpers for offline categorization eval artifacts.

The helpers are intentionally small and stdlib-only. They operate on explicit
file paths supplied by eval commands and never inspect FinScope runtime data.
"""

import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any


class JsonlError(ValueError):
    """Represent a JSONL parsing or serialization error with file context."""

    def __init__(self, path: Path, line_number: int | None, message: str) -> None:
        """Build an error message that includes the affected JSONL location."""
        self.path = path
        self.line_number = line_number
        self.message = message
        location = f"{path}:{line_number}" if line_number is not None else str(path)
        super().__init__(f"{location}: {message}")


def read_jsonl(path: str | Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield one parsed JSON object per non-empty JSONL line.

    Raises:
        JsonlError: If a line is not valid JSON or does not contain an object.
    """
    jsonl_path = Path(path)
    with jsonl_path.open(encoding="utf-8-sig") as jsonl_file:
        for line_number, line in enumerate(jsonl_file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise JsonlError(jsonl_path, line_number, f"invalid JSON: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise JsonlError(jsonl_path, line_number, "expected a JSON object")
            yield line_number, record


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Return all JSON objects from a JSONL file."""
    return [record for _, record in read_jsonl(path)]


def write_jsonl(path: str | Path, records: Iterable[Mapping[str, Any]], *, sort_keys: bool = True) -> int:
    """Write records to a JSONL file and return the number of rows written."""
    jsonl_path = Path(path)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as jsonl_file:
        for record in records:
            jsonl_file.write(json.dumps(record, ensure_ascii=True, sort_keys=sort_keys))
            jsonl_file.write("\n")
            row_count += 1
    return row_count
