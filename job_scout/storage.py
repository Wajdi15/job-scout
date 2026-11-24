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


def private_path(value: str | Path) -> Path:
    """Resolve an absolute path and reject paths in the public checkout."""
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("Use an absolute path for private data.")
        lexical_path = Path(os.path.abspath(path))
        resolved_path = path.resolve()
        if lexical_path.is_relative_to(PROJECT_ROOT) or resolved_path.is_relative_to(PROJECT_ROOT):
            raise ValueError("Choose a private data location outside the checkout.")
    except (OSError, RuntimeError):
        raise ValueError("The private data location could not be resolved.") from None
    return resolved_path


def private_data_dir(value: str | Path | None = None) -> Path:
    """Return a canonical path to a directory outside the checkout."""
    resolved = private_path(default_data_dir() if value is None else value)
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("The private data location must be a directory.")
    return resolved


def private_data_file(filename: str, data_dir: str | Path | None = None) -> Path:
    """Keep a managed file within the selected private directory."""
    if Path(filename).name != filename or filename in ("", ".", ".."):
        raise ValueError("Private storage expects a filename.")
    directory = private_data_dir(data_dir)
    path = directory / filename
    if private_path(path) != path or (path.exists() and not path.is_file()):
        raise ValueError("Use regular files in the private data directory.")
    return path


def preferences_path(data_dir: str | Path | None = None) -> Path:
    return private_data_file("search_preferences.yaml", data_dir)
