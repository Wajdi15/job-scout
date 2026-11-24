"""Read and initialize private search profiles."""

from dataclasses import dataclass
import os
from pathlib import Path

import yaml

from .storage import preferences_path, private_path


PROFILE_NAMES = ("industry", "research")
LEVELS = ("required", "preferred", "excluded")
FILTERS = (
    "regions", "remote_regions", "work_modes", "titles", "seniority", "topics",
    "role_types", "sponsorship", "phd_funding", "employers", "languages", "sources",
)
CHOICES = {
    "work_modes": {"remote", "hybrid", "onsite"},
    "sponsorship": {"available", "unavailable"},
    "phd_funding": {"funded", "unfunded"},
}
LIMITS = {"max_age_days": (1, 365), "min_alert_score": (0, 100)}
MAX_YAML_BYTES = 512 * 1024


@dataclass(frozen=True)
class Filter:
    required: tuple[str, ...]
    preferred: tuple[str, ...]
    excluded: tuple[str, ...]


@dataclass(frozen=True)
class SearchProfile:
    filters: dict[str, Filter]
    title_aliases: dict[str, tuple[str, ...]]
    max_age_days: int | None
    min_alert_score: int | None


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys instead of silently overriding them."""

    def construct_mapping(self, node, deep=False):
        mapping = super().construct_mapping(node, deep=deep)
        if len(mapping) != len(node.value):
            raise ValueError("YAML mapping keys must be unique.")
        return mapping


def _mapping(value, label):
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a mapping with text keys.")
    return value


def _terms(value, label):
    if not isinstance(value, list) or any(
        not isinstance(term, str) or not term.strip() for term in value
    ):
        raise ValueError(f"{label} must be a list of non-empty strings.")
    terms = tuple(term.strip() for term in value)
    if len({term.casefold() for term in terms}) != len(terms):
        raise ValueError(f"{label} contains duplicate values.")
    return terms


def _filter(value, name):
    rules = _mapping(value, name)
    if rules.keys() - set(LEVELS):
        raise ValueError(f"{name} supports required, preferred, and excluded.")
    groups = [_terms(rules.get(level, []), f"{name}.{level}") for level in LEVELS]
    all_terms = [term.casefold() for group in groups for term in group]
    if len(set(all_terms)) != len(all_terms):
        raise ValueError(f"{name} contains conflicting filter levels.")
    if name in CHOICES and set(all_terms) - CHOICES[name]:
        raise ValueError(f"{name} accepts: {', '.join(sorted(CHOICES[name]))}.")
    return Filter(*groups)


def _aliases(value, titles):
    aliases = _mapping(value, "title_aliases")
    canonical = {
        title.casefold(): title
        for title in (*titles.required, *titles.preferred, *titles.excluded)
    }
    claimed = set(canonical)
    result = {}
    for key, values in aliases.items():
        title = canonical.get(key.strip().casefold())
        if title is None or title in result:
            raise ValueError("Each title alias group must reference a distinct configured title.")
        terms = _terms(values, "title_aliases")
        for alias in terms:
            normalized = alias.casefold()
            if normalized in claimed:
                raise ValueError("Title aliases must be unique and distinct from configured titles.")
            claimed.add(normalized)
        result[title] = terms
    return result


def _limit(value, name):
    low, high = LIMITS[name]
    if value is not None and (type(value) is not int or not low <= value <= high):
        raise ValueError(f"{name} must be an integer from {low} to {high}.")
    return value


def _parse_yaml(text):
    try:
        return yaml.load(text, Loader=UniqueKeyLoader)
    except (yaml.YAMLError, RecursionError):
        raise ValueError("Invalid YAML. Check indentation, keys, and values.") from None


def parse_preferences(text: str) -> dict[str, SearchProfile]:
    """Validate YAML and return the profiles consumed by the scouts."""
    config = _mapping(_parse_yaml(text), "Preferences")
    version = config.get("version")
    if set(config) != {"version", "profiles"} or type(version) is not int or version != 1:
        raise ValueError("Specify version: 1 and a profiles mapping.")
    raw_profiles = _mapping(config["profiles"], "profiles")
    if set(raw_profiles) != set(PROFILE_NAMES):
        raise ValueError("Configure both industry and research profiles.")
    profiles = {}
    for name in PROFILE_NAMES:
        profile = _mapping(raw_profiles[name], name)
        if profile.keys() - {*FILTERS, *LIMITS, "title_aliases"}:
            raise ValueError(f"{name} profile has an unrecognized field.")
        rules = {key: _filter(profile.get(key, {}), key) for key in FILTERS}
        profiles[name] = SearchProfile(
            filters=rules,
            title_aliases=_aliases(profile.get("title_aliases", {}), rules["titles"]),
            max_age_days=_limit(profile.get("max_age_days"), "max_age_days"),
            min_alert_score=_limit(profile.get("min_alert_score"), "min_alert_score"),
        )
    return profiles


def _read_yaml(path: Path) -> str:
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


def load_preferences(data_dir: str | Path | None = None) -> dict[str, SearchProfile]:
    return parse_preferences(_read_yaml(preferences_path(data_dir)))


def suggested_titles(candidate_facts: str | Path) -> list[str]:
    """Suggest search titles only from experience entries that carry evidence."""
    facts = _mapping(_parse_yaml(_read_yaml(private_path(candidate_facts))), "Candidate facts")
    experience = facts.get("experience", [])
    if not isinstance(experience, list):
        raise ValueError("Candidate facts experience must be a list.")

    suggestions = {}
    signals = (
        ("computer vision", "Computer Vision Engineer"),
        ("medical imaging", "Medical Imaging Engineer"),
        ("fastapi", "Backend Engineer"),
        ("machine learning", "Machine Learning Engineer"),
    )
    for role in experience:
        if not isinstance(role, dict) or not isinstance(role.get("evidence"), list):
            continue
        evidence = [
            item["statement"] for item in role["evidence"]
            if isinstance(item, dict) and item.get("id")
            and isinstance(item.get("statement"), str) and item["statement"].strip()
        ]
        if not evidence:
            continue
        title = role.get("title")
        if isinstance(title, str) and title.strip():
            title = title.strip()
            suggestions.setdefault(title.casefold(), title)
        for signal, target in signals:
            if any(signal in statement.casefold() for statement in evidence):
                suggestions.setdefault(target.casefold(), target)
    return list(suggestions.values())


def _open_private_file(path, flags):
    return os.open(path, flags, 0o600)


def initialize_preferences(
    data_dir: str | Path | None = None, candidate_facts: str | Path | None = None
) -> None:
    """Create the two profiles once, without overwriting a user edit."""
    path = preferences_path(data_dir)
    if path.exists():
        raise ValueError("search_preferences.yaml already exists. Edit it directly.")
    titles = suggested_titles(candidate_facts) if candidate_facts is not None else []
    config = {
        "version": 1,
        "profiles": {
            "industry": {"titles": {"preferred": titles}},
            "research": {"titles": {"preferred": ["Research Engineer", "PhD"]}},
        },
    }
    text = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    parse_preferences(text)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with open(path, "x", encoding="utf-8", newline="\n", opener=_open_private_file) as output:
            output.write(text)
    except FileExistsError:
        raise ValueError("search_preferences.yaml already exists. Edit it directly.") from None
    except OSError:
        raise ValueError("Could not create search_preferences.yaml in private storage.") from None
