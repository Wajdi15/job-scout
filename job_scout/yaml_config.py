"""Safely load private YAML configuration."""

from pathlib import Path

import yaml


MAX_YAML_BYTES = 512 * 1024


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys instead of silently overriding them."""

    def construct_mapping(self, node, deep=False):
        mapping = super().construct_mapping(node, deep=deep)
        if len(mapping) != len(node.value):
            raise ValueError("YAML mapping keys must be unique.")
        return mapping


def parse_yaml(text):
    try:
        return yaml.load(text, Loader=UniqueKeyLoader)
    except (yaml.YAMLError, RecursionError):
        raise ValueError("Invalid YAML. Check indentation, keys, and values.") from None


def read_private_yaml(path: Path) -> str:
    if not path.is_file():
        raise ValueError("The requested private YAML file does not exist or is not a regular file.")
    try:
        with path.open("rb") as source:
            data = source.read(MAX_YAML_BYTES + 1)
        if len(data) > MAX_YAML_BYTES:
            raise ValueError("The private YAML file exceeds the size limit.")
        return data.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise ValueError("Could not read the private YAML file as UTF-8.") from None
