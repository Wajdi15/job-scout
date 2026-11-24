"""Resolve private storage paths without creating files."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def default_data_dir() -> Path:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "JobScout"
        return Path.home() / "AppData" / "Local" / "JobScout"
    return Path.home() / ".local" / "share" / "job-scout"


def private_data_dir(value: str | Path | None = None) -> Path:
    """Return a canonical path outside the project checkout."""
    try:
        path = default_data_dir() if value is None else Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("Use an absolute path for the private data directory.")
        lexical_path = Path(os.path.abspath(path))
        resolved_path = path.resolve()
        if lexical_path.is_relative_to(PROJECT_ROOT) or resolved_path.is_relative_to(PROJECT_ROOT):
            raise ValueError("Choose a private data directory outside the checkout.")
        if resolved_path.exists() and not resolved_path.is_dir():
            raise ValueError("The private data location must be a directory.")
    except (OSError, RuntimeError):
        raise ValueError("The private data directory could not be resolved.") from None
    return resolved_path
