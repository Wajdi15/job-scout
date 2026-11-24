"""Read public job feeds using fixed HTTPS endpoints."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from http.client import HTTPException
from collections.abc import Callable
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from .storage import private_data_file
from .yaml_config import parse_yaml, read_private_yaml


TOKEN = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
PAGE_SIZE = 100
MAX_PAGES = 10
BATH_FEED = "https://www.bath.ac.uk/jobs/rss/rss.aspx?cat=391"


@dataclass(frozen=True)
class Source:
    provider: str
    board: str
    employer: str
    instance: str = "global"

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.instance}:{self.board.casefold()}"


@dataclass(frozen=True)
class Posting:
    source_id: str
    title: str
    employer: str
    url: str
    description: str = ""
    location: str = ""
    workplace: str = ""
    role_type: str = ""
    published_at: str | None = None
    deadline: str | None = None


@dataclass(frozen=True)
class Batch:
    postings: list[Posting]
    rejected: int = 0


class SourceError(Exception):
    """A safe message describing a source failure."""


def parse_sources(text: str) -> list[Source]:
    config = parse_yaml(text)
    if (
        not isinstance(config, dict)
        or set(config) != {"version", "sources"}
        or type(config.get("version")) is not int
        or config["version"] != 1
    ):
        raise ValueError("Job sources must specify version: 1 and a sources list.")
    entries = config["sources"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 50:
        raise ValueError("Configure between 1 and 50 job sources.")
    sources = []
    keys = set()
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or entry.keys() - {"provider", "board", "employer", "instance"}:
            raise ValueError(f"Source {index} has invalid fields.")
        provider, board, employer = (entry.get(key) for key in ("provider", "board", "employer"))
        instance = entry.get("instance", "global")
        if provider not in ("greenhouse", "lever", "bath"):
            raise ValueError(f"Source {index} must use greenhouse, lever, or bath.")
        if not isinstance(board, str) or not TOKEN.fullmatch(board):
            raise ValueError(f"Source {index} requires a board identifier, without a URL.")
        if not isinstance(employer, str) or not employer.strip() or len(employer) > 120:
            raise ValueError(f"Source {index} requires an employer name.")
        if instance not in ("global", "eu") or (instance != "global" and provider != "lever"):
            raise ValueError(f"Source {index} has an invalid instance. Lever supports global and eu.")
        if provider == "bath" and board != "research":
            raise ValueError(f"Source {index} must use research as the Bath board identifier.")
        source = Source(provider, board, employer.strip(), instance)
        if source.key in keys:
            raise ValueError(f"Source {index} is already configured.")
        keys.add(source.key)
        sources.append(source)
    return sources


def load_sources(data_dir=None) -> list[Source]:
    return parse_sources(read_private_yaml(private_data_file("job_sources.yaml", data_dir)))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise SourceError("The source returned a redirect.")


def download(url: str, accept: str) -> bytes:
    request = Request(url, headers={"User-Agent": "JobScout/0.1 (public jobs)", "Accept": accept})
    try:
        with build_opener(NoRedirect()).open(request, timeout=15) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        code = error.code
        error.close()
        raise SourceError(f"HTTP {code}") from None
    except (URLError, OSError, HTTPException):
        raise SourceError("Network or TLS error") from None
    if len(payload) > MAX_RESPONSE_BYTES:
        raise SourceError("Response exceeds the size limit")
    return payload


def _json(fetch, url):
    try:
        return json.loads(fetch(url, "application/json"))
    except (ValueError, UnicodeError, RecursionError):
        raise SourceError("Invalid JSON feed") from None


def _text(value, limit=400):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden_tag = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden_tag = tag

    def handle_endtag(self, tag):
        if tag == self.hidden_tag:
            self.hidden_tag = None

    def handle_data(self, data):
        if self.hidden_tag is None:
            self.parts.append(data)


def _plain(value):
    if not isinstance(value, str):
        return ""
    parser = PlainText()
    html = unescape(value)
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, ValueError):
        return ""
    return _text(" ".join(parser.parts), 50000)


def _iso(value):
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _epoch_millis(value):
    if type(value) is not int or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _feed_date(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def canonical_url(value: str) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    if any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        return None
    try:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username is not None or url.port not in (None, 443):
            return None
    except ValueError:
        return None
    host = "job-boards.greenhouse.io" if url.hostname == "boards.greenhouse.io" else url.hostname
    if "." not in host or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        return None
    tracking = {"gh_src", "lever-source", "lever-origin"}
    query = [
        (key, value) for key, value in parse_qsl(url.query, keep_blank_values=True)
        if key.casefold() not in tracking and not key.casefold().startswith("utm_")
    ]
    return urlunsplit(("https", host, url.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


def _batch(rows, convert):
    postings = []
    for row in rows:
        posting = convert(row)
        if posting is not None:
            postings.append(posting)
    return Batch(postings, len(rows) - len(postings))


def _greenhouse(source, fetch):
    raw = _json(fetch, f"https://boards-api.greenhouse.io/v1/boards/{source.board}/jobs?content=true")
    if not isinstance(raw, dict) or not isinstance(raw.get("jobs"), list):
        raise SourceError("Unexpected Greenhouse feed structure")
    rows = raw["jobs"]

    def convert(row):
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0:
            return None
        title = _text(row.get("title"))
        if not title:
            return None
        posting_id = str(row["id"])
        url = canonical_url(row.get("absolute_url")) or (
            f"https://job-boards.greenhouse.io/{source.board}/jobs/{posting_id}"
        )
        location = row.get("location")
        return Posting(
            posting_id, title, _text(row.get("company_name")) or source.employer, url,
            description=_plain(row.get("content")),
            location=_text(location.get("name")) if isinstance(location, dict) else "",
            published_at=_iso(row.get("first_published")),
            deadline=_iso(row.get("application_deadline")),
        )

    # The API includes prospect forms without an internal job ID.
    rows = [row for row in rows if not isinstance(row, dict) or "internal_job_id" not in row or row["internal_job_id"] is not None]
    return _batch(rows, convert)


def _lever(source, fetch):
    host = "api.eu.lever.co" if source.instance == "eu" else "api.lever.co"
    board_host = "jobs.eu.lever.co" if source.instance == "eu" else "jobs.lever.co"
    rows = []
    for offset in range(0, PAGE_SIZE * MAX_PAGES, PAGE_SIZE):
        url = f"https://{host}/v0/postings/{source.board}?mode=json&limit={PAGE_SIZE}&skip={offset}"
        page = _json(fetch, url)
        if not isinstance(page, list) or len(page) > PAGE_SIZE:
            raise SourceError("Unexpected Lever feed structure")
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
    else:
        raise SourceError("Lever feed exceeded the page limit")

    def convert(row):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not TOKEN.fullmatch(row["id"]):
            return None
        title = _text(row.get("text"))
        if not title:
            return None
        categories = row.get("categories")
        categories = categories if isinstance(categories, dict) else {}
        sections = row.get("lists")
        sections = sections if isinstance(sections, list) else []
        text = [
            _text(row.get("descriptionPlain"), 50000) or _plain(row.get("description")),
            *(_plain(section.get("content")) for section in sections if isinstance(section, dict)),
            _text(row.get("additionalPlain"), 50000),
        ]
        hosted = canonical_url(row.get("hostedUrl"))
        if hosted is not None:
            parts = urlsplit(hosted)
            expected = f"/{source.board}/{row['id']}"
            if parts.hostname != board_host or parts.path.casefold() != expected.casefold():
                hosted = None
        return Posting(
            row["id"], title, source.employer,
            hosted or f"https://{board_host}/{source.board}/{row['id']}",
            description=_text(" ".join(part for part in text if part), 50000),
            location=_text(categories.get("location")),
            workplace=_text(row.get("workplaceType")),
            role_type=_text(categories.get("commitment")),
            published_at=_iso(row.get("publishedAt")) or _epoch_millis(row.get("publishedAt")),
            deadline=_iso(row.get("applicationDeadline")),
        )

    return _batch(rows, convert)


def _bath_deadline(description):
    dates = re.findall(r"Closing Date:\s*(\d{1,2} [A-Za-z]{3} \d{4})\b", description, re.IGNORECASE)
    if not dates:
        return None
    try:
        return parsedate_to_datetime(f"{dates[-1]} 00:00:00 GMT").date().isoformat()
    except ValueError:
        return None


def _bath(source, fetch):
    try:
        xml = fetch(BATH_FEED, "application/rss+xml, application/xml, text/xml").decode("utf-8-sig")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
            raise SourceError("RSS document declarations are unsupported")
        root = ElementTree.fromstring(xml)
    except (ElementTree.ParseError, UnicodeError):
        raise SourceError("Invalid Bath RSS feed") from None
    if root.tag != "rss" or root.find("channel") is None:
        raise SourceError("Unexpected Bath RSS structure")

    def convert(item):
        link = canonical_url(item.findtext("link"))
        title = _text(item.findtext("title"))
        if link is None or not title:
            return None
        parts = urlsplit(link)
        reference = dict(parse_qsl(parts.query)).get("ref", "")
        if parts.hostname != "www.bath.ac.uk" or not TOKEN.fullmatch(reference):
            return None
        description = _plain(item.findtext("description"))
        return Posting(
            reference.upper(), title, source.employer,
            f"https://www.bath.ac.uk/jobs/Vacancy.aspx?ref={reference.upper()}",
            description=description,
            published_at=_feed_date(item.findtext("pubDate")),
            deadline=_iso(item.findtext("closingDate")) or _bath_deadline(description),
        )

    return _batch(root.findall("./channel/item"), convert)


def fetch_source(source: Source, fetch: Callable[[str, str], bytes] = download) -> Batch:
    return {"greenhouse": _greenhouse, "lever": _lever, "bath": _bath}[source.provider](source, fetch)
