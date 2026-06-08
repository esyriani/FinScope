"""Command-line entry point for FinScope."""

import sys
from pathlib import Path

sys.dont_write_bytecode = True

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_app import create_app  # noqa: E402
from finance_app.core.config import settings  # noqa: E402
from finance_app.database.connection import init_db  # noqa: E402

app = create_app()


if __name__ == "__main__":
    init_db()
    app.run(
        host=settings.server_host,
        port=settings.server_port,
        debug=settings.server_debug,
        use_reloader=False,
    )
