"""Static asset URL helpers.

Builds Flask static URLs with content-based cache keys for local assets.
Depends on the application's configured static folder and Flask's url routing.
"""

from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from flask import url_for


ASSET_HASH_LENGTH = 12


def static_asset_hash(filename, static_folder):
    """Return a short content hash for a file in the configured static folder.

    Args:
        filename: Relative path under the Flask static folder.
        static_folder: Root directory containing static assets.

    Returns:
        A short hexadecimal content hash when the file exists under the static
        root, otherwise None.
    """
    static_root = Path(static_folder).resolve()
    asset_path = (static_root / filename).resolve()

    try:
        asset_path.relative_to(static_root)
    except ValueError:
        return None

    try:
        asset_stat = asset_path.stat()
    except OSError:
        return None

    if not asset_path.is_file():
        return None

    return _static_asset_hash_for_state(
        str(asset_path),
        asset_stat.st_mtime_ns,
        asset_stat.st_size,
    )


@lru_cache(maxsize=512)
def _static_asset_hash_for_state(asset_path, _mtime_ns, _size):
    """Hash a static file, invalidating the cache when its filesystem state changes."""
    return sha256(Path(asset_path).read_bytes()).hexdigest()[:ASSET_HASH_LENGTH]


def register_asset_helpers(app):
    """Register template helpers that produce hashed local static asset URLs.

    The registered static_asset helper appends a v query parameter containing a
    content hash for files found in the application's static folder. Missing
    files still resolve through Flask's static endpoint so template errors stay
    easy to diagnose during development.
    """

    def static_asset(filename):
        """Return a Flask static URL with a content hash query string when available."""
        version = static_asset_hash(filename, app.static_folder)
        if version is None:
            return url_for("static", filename=filename)
        return url_for("static", filename=filename, v=version)

    @app.context_processor
    def inject_asset_helpers():
        """Expose static asset helpers to every template render."""
        return {"static_asset": static_asset}
