"""Rank a local collection and write private job reports."""

from contextlib import closing
from datetime import date, datetime
from html import escape
import json
from pathlib import Path
import re
import sqlite3
from tempfile import NamedTemporaryFile

from .eligibility import assess_eligibility, eligibility_status, finding, phd_title
from .jobs import DATABASE_NAME
from .matching import extract_criteria, load_candidate, match_criteria, normalized, phrase_pattern
from .preferences import SearchProfile, load_preferences
from .sources import canonical_url
from .research import RESEARCH_MARKER, RESEARCH_PROVIDER_NAMES
from .storage import private_data_file


REPORT_JSON = "ranked_jobs.json"
REPORT_HTML = "ranked_jobs.html"
SHORTLIST_LIMIT = 10
RESEARCH_ROLE = re.compile(r"\b(?:research engineer|research scientist|research associate|"
                           r"research fellow|ingenieur de recherche|doctorant|postdoc)\b")
FILTER_FIELDS = {
    "titles": "title",
    "regions": "location",
    "work_modes": "workplace",
    "seniority": "title",
    "topics": "description",
    "role_types": "role_type",
    "employers": "employer",
}
# Within one filter, values are alternatives. Known categorical fields are decisive.
DECISIVE = {"titles", "work_modes", "sources", "role_types"}


def _phrase(value, terms):
    return bool(terms and phrase_pattern(tuple(terms)).search(normalized(value)))


def _filter_match(key, terms, posting, profile):
    if not terms:
        return False
    if key == "sources":
        return any(source.split(":", 1)[0] in {term.casefold() for term in terms}
                   for source in posting["sources"])
    if key == "titles":
        expanded = [*terms]
        for title, aliases in profile.title_aliases.items():
            if title in terms:
                expanded.extend(aliases)
        return _phrase(posting["title"], expanded)
    value = posting.get(FILTER_FIELDS.get(key, ""), "")
    if key == "work_modes":
        value = value.replace("on-site", "onsite").replace("on site", "onsite")
    return _phrase(value, terms)


def _preferences(posting, profile):
    excluded = []
    pending = []
    preferred = []
    for field in (*FILTER_FIELDS, "sources", "remote_regions", "languages"):
        rule = profile.filters[field]
        if _filter_match(field, rule.excluded, posting, profile):
            excluded.append(f"Matches an excluded {field.replace('_', ' ')} value.")
        if rule.required and not _filter_match(field, rule.required, posting, profile):
            value = posting.get(FILTER_FIELDS.get(field, ""), "")
            if field == "sources" or (field in DECISIVE and value):
                excluded.append(f"Does not match the required {field.replace('_', ' ')} filter.")
            else:
                pending.append(f"Verify the required {field.replace('_', ' ')} preference.")
        if _filter_match(field, rule.preferred, posting, profile):
            preferred.append(field)
    return excluded, pending, preferred


def _is_older(value, max_days, now):
    if not value or max_days is None:
        return False
    try:
        value = date.fromisoformat(value[:10])
    except ValueError:
        return False
    return (now.date() - value).days > max_days


def _profile_name(posting):
    if any(source.split(":", 1)[0] in RESEARCH_PROVIDER_NAMES | {"bath"} for source in posting["sources"]):
        return "research"
    title = posting["title"]
    return "research" if phd_title(title) or RESEARCH_ROLE.search(normalized(title)) else "industry"


def _postings(data_dir):
    path = private_data_file(DATABASE_NAME, data_dir)
    if not path.is_file():
        raise ValueError("Collect jobs before ranking them.")
    for suffix in ("-journal", "-wal", "-shm"):
        private_data_file(DATABASE_NAME + suffix, path.parent)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        jobs = {row["id"]: {**dict(row), "sources": [], "seen_in_latest_feed": False}
                for row in db.execute("SELECT * FROM postings")}
        for row in db.execute("""
            WITH latest AS (SELECT source, max(id) AS id FROM source_checks GROUP BY source)
            SELECT s.job_id, s.source, s.last_seen = c.checked_at AND c.status IN ('ok','partial') AS seen
            FROM posting_sources AS s
            LEFT JOIN latest ON latest.source = s.source
            LEFT JOIN source_checks AS c ON c.id = latest.id
            ORDER BY s.source
        """):
            job = jobs[row["job_id"]]
            if row["source"] not in job["sources"]:
                job["sources"].append(row["source"])
            job["seen_in_latest_feed"] |= bool(row["seen"])
    return jobs.values()


def rank_posting(posting, profile: SearchProfile, candidate, now):
    criteria = extract_criteria(posting["description"])
    score, matches = match_criteria(criteria, candidate)
    checks = assess_eligibility(posting, profile, candidate, now)
    excluded, pending, preferred = _preferences(posting, profile)
    if _is_older(posting["published_at"], profile.max_age_days, now):
        excluded.append("The published date is outside the configured age limit.")
    elif profile.max_age_days is not None and not posting["published_at"]:
        pending.append("The source did not publish a date for the age filter.")
    if not posting["seen_in_latest_feed"]:
        checks.append(finding("Latest source check", "unknown", "Verify that this posting is still listed."))
    checks.extend(finding("Search preference", "unknown", requirement) for requirement in pending)
    status = eligibility_status(checks)
    evidence_matches = sum(match["match"] == "direct" for match in matches)
    # A single recognized criterion can have a high score without enough support to shortlist.
    shortlist = (
        not excluded and posting["seen_in_latest_feed"] and status != "unmet"
        and score is not None and score >= 50 and len(matches) >= 2 and evidence_matches > 0
    )
    return {
        "id": posting["id"], "title": posting["title"], "employer": posting["employer"],
        "url": canonical_url(posting["url"]), "location": posting["location"],
        "sources": posting["sources"], "published_at": posting["published_at"], "deadline": posting["deadline"],
        "public_page_summary": bool(posting["sources"]) and all(RESEARCH_MARKER in source for source in posting["sources"]),
        "first_seen": posting["first_seen"], "last_seen": posting["last_seen"],
        "fit_score": score, "match_count": len(matches), "direct_matches": evidence_matches,
        "criteria": matches, "eligibility": status, "eligibility_checks": checks,
        "excluded": excluded, "preferred_filters": preferred, "shortlist": shortlist,
    }


def _order(job):
    score = job["fit_score"]
    # Preference and evidence counts break ties without changing the evidence score.
    return (job["eligibility"] != "met", "titles" not in job["preferred_filters"],
            -(score if score is not None else -1),
            -len(job["preferred_filters"]), -job["direct_matches"], -job["match_count"], job["id"])


def _atomic_write(path: Path, content: str):
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                dir=path.parent, prefix=".ranking-", delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _e(value):
    return escape(str(value), quote=True)


def _card(job):
    title = _e(job["title"])
    link = (f'<a href="{_e(job["url"])}" rel="noreferrer">{title}</a>' if job["url"] else title)
    score = "insufficient evidence" if job["fit_score"] is None else f'{job["fit_score"]}/100'
    summary = (f'{link} · {_e(job["employer"])} · Fit {_e(score)} '
               f'· Eligibility {_e(job["eligibility"])} · {job["match_count"]} criteria')
    eligibility = "".join(
        f'<li><b>{_e(check["check"])} · {_e(check["status"])}</b> — {_e(check["detail"])}'
        + (f' <q>{_e(check["posting_excerpt"])}</q>' if check["posting_excerpt"] else "")
        + (f' <code>{_e(check["evidence_id"])}</code>' if check["evidence_id"] else "") + "</li>"
        for check in job["eligibility_checks"]
    )
    criteria = "".join(
        f'<li><b>{_e(match["criterion"])} · {_e(match["priority"])} · {_e(match["match"])}</b>'
        f' <q>{_e(match["posting_excerpt"])}</q>'
        + (f' <code>{_e(match["evidence_id"])}</code> {_e(match["evidence"])}'
           f' <small>({_e(match["scope"])})</small>' if match["evidence"] else "")
        + (f' Related experience: {_e(match["related_skill"])}.' if match["related_skill"] else "") + "</li>"
        for match in job["criteria"]
    )
    excluded = "".join(f"<li>{_e(item)}</li>" for item in job["excluded"])
    metadata = (f'Job #{job["id"]} · {_e(job["location"] or "Location unconfirmed")}'
                f' · First seen {_e(job["first_seen"][:10])} · Last seen {_e(job["last_seen"][:10])}')
    if job.get("public_page_summary"):
        metadata += " · Requirements summarized from the public posting"
    return (f"<details><summary>{summary}</summary><p>{metadata}</p>"
            + (f"<p>Outside search preferences</p><ul>{excluded}</ul>" if excluded else "")
            + f"<h4>Eligibility</h4><ul>{eligibility}</ul><h4>Why it matches and where it falls short</h4>"
            + (f"<ul>{criteria}</ul>" if criteria else "<p>No technical criteria recognized in the collected description.</p>")
            + "</details>")


def render_report(report):
    sections = []
    selected = set(report.get("new_shortlist", []))
    for name, label in (("industry", "Industry"), ("research", "Research and PhD")):
        jobs = report["profiles"][name]
        if "new_shortlist" in report:
            shortlisted = [job for job in jobs if job["id"] in selected]
        else:
            shortlisted = [job for job in jobs if job["shortlist"]][:SHORTLIST_LIMIT]
        included = {job["id"] for job in shortlisted}
        others = [job for job in jobs if job["id"] not in included]
        cards = "".join(_card(job) for job in shortlisted) or "<p>No postings met the shortlist criteria.</p>"
        rest = "".join(_card(job) for job in others)
        sections.append(f"<section><h2>{label} · {len(jobs)} jobs</h2>{cards}"
                        f"<details><summary>Other reviewed postings ({len(others)})</summary>{rest}</details></section>")
    search_details = ""
    if "new_shortlist" in report:
        count = len(selected)
        coverage = "".join(
            f'<li>{_e(check["source"])} · {_e(check["status"])} · {check["seen"]} seen · {check["added"]} new'
            f' · {check["rejected"]} rejected</li>'
            for check in report["coverage"]
        )
        skipped = ", ".join(report["skipped_research"])
        search_details = (
            f"<p><b>{count} new shortlisted jobs</b> · target 30–50</p>"
            + ("<p>Fewer results met the ranking and eligibility criteria on this run.</p>" if count < 30 else "")
            + f"<details><summary>Sources checked</summary><ul>{coverage}</ul>"
            + (f"<p>Web research skipped: {_e(skipped)}</p>" if skipped else "")
            + "</details>"
        )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        '<title>Private job rankings</title>'
        '<style>body{font:16px/1.55 system-ui;max-width:65rem;margin:2rem auto;padding:0 1rem;color:#17242b}'
        'details{border:1px solid #ccd7da;border-radius:.4rem;padding:.7rem;margin:.55rem 0}'
        'summary{cursor:pointer}a{color:#075a78}li{margin:.4rem 0}small{color:#41565e}'
        'h4{margin-bottom:.25rem}section{margin:2rem 0}</style>'
        '<body><h1>Private job rankings</h1>'
        f'<p>{report["job_count"]} postings evaluated · {_e(report["generated_at"][:10])}</p>'
        '<p>Fit reflects recognized technical criteria and verified evidence. '
        'Required criteria carry 3 points, responsibilities 2, and preferred criteria 1. '
        'Direct evidence earns full credit, listed experience 60%, related experience 35%, and gaps 0%. '
        'Open a job to see the evidence and eligibility checks.</p>'
        + search_details + "".join(sections) + "</body></html>"
    )


def rank_jobs(candidate_facts=None, data_dir=None, *, now=None, new_since=None,
              coverage=(), skipped_research=()):
    profiles = load_preferences(data_dir)
    source = candidate_facts if candidate_facts is not None else private_data_file("candidate_facts.yaml", data_dir)
    candidate = load_candidate(source)
    now = now if now is not None else datetime.now().astimezone()
    grouped = {name: [] for name in profiles}
    for posting in _postings(data_dir):
        name = _profile_name(posting)
        grouped[name].append(rank_posting(posting, profiles[name], candidate, now))
    for jobs in grouped.values():
        jobs.sort(key=lambda job: (not job["shortlist"], bool(job["excluded"]), *_order(job)))
    report = {
        "generated_at": now.isoformat(), "job_count": sum(map(len, grouped.values())),
        "shortlist_count": sum(min(SHORTLIST_LIMIT, sum(job["shortlist"] for job in jobs))
                               for jobs in grouped.values()),
        "profiles": grouped,
    }
    if new_since is not None:
        fresh = [
            job for jobs in grouped.values() for job in jobs
            if job["shortlist"] and datetime.fromisoformat(job["first_seen"]) >= new_since
        ]
        fresh.sort(key=_order)
        report["new_shortlist"] = [job["id"] for job in fresh[:50]]
        report["shortlist_count"] = len(report["new_shortlist"])
        report["coverage"] = coverage
        report["skipped_research"] = skipped_research
    json_path = private_data_file(REPORT_JSON, data_dir)
    html_path = private_data_file(REPORT_HTML, data_dir)
    _atomic_write(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(html_path, render_report(report))
    return report["job_count"], report["shortlist_count"]
