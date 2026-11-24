"""Local, explainable comparison of posting criteria with verified evidence."""

from dataclasses import dataclass
import re
import unicodedata

from .storage import private_path
from .yaml_config import parse_yaml, read_private_yaml


# Aliases describe the same skill. Families describe related, transferable skills.
VOCABULARY = {
    "Python": ("python",), "JavaScript": ("javascript", "ecmascript"),
    "TypeScript": ("typescript",), "Java": ("java",), "C#": ("c#", "c sharp"),
    "C++": ("c++",), "Go": ("golang",), "Rust": ("rust",), "Ruby": ("ruby",),
    "PHP": ("php",), "SQL": ("sql", "structured query language"),
    "PostgreSQL": ("postgresql", "postgres"), "MySQL": ("mysql", "mariadb"),
    "MongoDB": ("mongodb",), "Redis": ("redis",),
    "FastAPI": ("fastapi", "fast api"), "Django": ("django",),
    "Flask": ("flask",), "Spring Boot": ("spring boot",),
    ".NET": (".net", "dotnet", "asp.net"), "Node.js": ("node.js", "nodejs", "node js"),
    "Ruby on Rails": ("ruby on rails",), "Express": ("express.js", "expressjs"),
    "React": ("react", "react.js", "reactjs"), "Next.js": ("next.js", "nextjs", "next js"),
    "Angular": ("angular",), "Vue": ("vue.js", "vuejs", "vue js"),
    "React Native": ("react native",), "Flutter": ("flutter",), "HTML": ("html", "html5"),
    "CSS": ("css", "css3"), "REST APIs": ("rest api", "restful", "rest apis"),
    "GraphQL": ("graphql",), "Microservices": ("microservices", "micro services"),
    "Full stack development": ("full stack", "fullstack"),
    "Backend development": ("backend development", "backend services", "back end services"),
    "Frontend development": ("frontend development", "front end development"),
    "Docker": ("docker",), "Kubernetes": ("kubernetes", "k8s"),
    "AWS": ("aws", "amazon web services"), "Azure": ("azure",), "Google Cloud": ("gcp", "google cloud"),
    "CI/CD": ("ci cd", "continuous integration", "continuous delivery"), "Git": ("git",),
    "Terraform": ("terraform",), "Linux": ("linux",),
    "Machine learning": ("machine learning", "apprentissage automatique"),
    "Deep learning": ("deep learning", "apprentissage profond"),
    "Computer vision": ("computer vision", "vision par ordinateur"),
    "Image segmentation": ("image segmentation", "segmentation d images", "segmentation d image"),
    "Medical imaging": ("medical imaging", "imagerie medicale", "mri", "irm cardiaque"),
    "PyTorch": ("pytorch",), "TensorFlow": ("tensorflow",),
    "Model deployment": ("model deployment", "model serving", "deploy ml models", "deploy models"),
    "Model evaluation": ("model evaluation", "model validation", "evaluate models", "dice score"),
    "LLMs": ("llm", "llms", "large language model", "large language models"),
    "RAG": ("rag", "retrieval augmented generation"),
    "Data pipelines": ("data pipelines", "data pipeline", "data ingestion", "etl"),
    "Data analysis": ("data analysis", "analyse de donnees"),
    "Scientific computing": ("scientific computing", "calcul scientifique"),
    "Distributed systems": ("distributed systems", "systemes distribues"),
    "Automated testing": ("automated testing", "unit tests", "integration tests", "test automation"),
    "Image registration": ("image registration", "recalage d images"),
    "Publications": ("peer reviewed publications", "publish research", "scientific publications"),
}
FAMILIES = (
    {"FastAPI", "Django", "Flask", "Spring Boot", ".NET", "Node.js", "Express", "Ruby on Rails"},
    {"React", "Next.js", "Angular", "Vue"},
    {"PostgreSQL", "MySQL"},
    {"PyTorch", "TensorFlow"},
    {"AWS", "Azure", "Google Cloud"},
)


def normalized(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        re.sub(r"[^a-z0-9+#]+", " ", "".join(c for c in text if not unicodedata.combining(c))).split()
    )


def phrase_pattern(aliases: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(alias) for alias in sorted({normalized(value) for value in aliases}, key=len, reverse=True)
        if alias
    )
    return re.compile(rf"(?<![a-z0-9+#])(?:{alternatives})(?![a-z0-9+#])" if alternatives else r"(?!)")


PATTERNS = {label: phrase_pattern(values) for label, values in VOCABULARY.items()}
PATTERNS["React"] = re.compile(r"(?<![a-z0-9+#])react(?: js|js)?(?! native)(?![a-z0-9+#])")
WEIGHTS = {"required": 3, "responsibility": 2, "preferred": 1}

# Titles never contribute to the score. Only technical criteria extracted from the body do.
HEADINGS = re.compile(
    r"(?P<required>\b(?:minimum qualifications|required qualifications|required skills|job requirements|"
    r"what you(?:'|’)ll (?:bring|need)|what you (?:bring|need)|what we(?:'|’)re looking for|"
    r"what (?:we|you) (?:are looking for|are seeking)|about you|"
    r"qualifications essentielles|comp[ée]tences requises|profil recherch[ée]|"
    r"requirements(?=\s*:))\b|(?-i:\bRequirements\b))|"
    r"(?P<preferred>\b(?:preferred qualifications|preferred skills|nice to have|bonus points|"
    r"desirable qualifications|qualifications souhait[ée]es|un plus)\b)|"
    r"(?P<responsibility>\b(?:responsibilities|what you(?:'|’)ll do|your role|"
    r"main activities|your mission|vos missions|missions principales)\b)|"
    r"(?P<ignore>\b(?:benefits|about the company|equal opportunity|our values|how to apply|"
    r"compensation and benefits|avantages)\s*:)",
    re.IGNORECASE,
)
REQUIRED = re.compile(
    r"\b(?:must(?: have)?|minimum|at least|proficie(?:nt|ncy)|strong knowledge|"
    r"experience (?:in|with|using|developing|building)|required|"
    r"ma[iî]trise|exp[ée]rience (?:en|avec|de)|obligatoire|requis(?:e)?)\b", re.I,
)
PREFERRED = re.compile(
    r"\b(?:nice to have|preferred|a plus|desirable|bonus|ideally|"
    r"id[ée]alement|souhait[ée]e?|un atout|serait un plus)\b", re.I,
)
WORK = re.compile(
    r"\b(?:build|develop|implement|design|maintain|deploy|train|evaluate|"
    r"concevoir|d[ée]velopper|entra[iî]ner|d[ée]ployer|maintenir)\b", re.I,
)
NEGATED = re.compile(r"\b(?:no experience (?:with|in)|without (?:using|experience in)|not required)\b", re.I)
CLAUSES = re.compile(r"\n+|(?<=[.!?;])\s+|\s*[•●▪]\s*")


@dataclass(frozen=True)
class Evidence:
    id: str
    statement: str
    scope: str
    listed: bool = False


@dataclass(frozen=True)
class Candidate:
    evidence: dict[str, Evidence]
    listed: dict[str, Evidence]
    degrees: tuple[dict, ...]
    languages: tuple[dict, ...]
    needs_sponsorship: bool


@dataclass(frozen=True)
class Criterion:
    skills: tuple[str, ...]
    priority: str
    excerpt: str


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _record(value) -> dict:
    return value if isinstance(value, dict) else {}


def _signals(text: str):
    folded = normalized(text)
    return [label for label, pattern in PATTERNS.items() if pattern.search(folded)]


def load_candidate(path) -> Candidate:
    facts = _record(parse_yaml(read_private_yaml(private_path(path))))
    direct, listed = {}, {}
    for index, entry in enumerate(_list(facts.get("experience"))):
        role = _record(entry)
        for raw in _list(role.get("evidence")):
            record = _record(raw)
            identifier, statement = _text(record.get("id")), _text(record.get("statement"))
            if identifier and statement and not NEGATED.search(statement):
                proof = Evidence(identifier, statement, _text(record.get("scope")) or "verified experience")
                for label in _signals(statement):
                    if label not in direct or proof.scope == "professional":
                        direct[label] = proof
        for technology in _list(role.get("technologies")):
            label_text = _text(technology)
            for label in _signals(label_text):
                listed.setdefault(label, Evidence(
                    f"experience[{index}].technologies",
                    f"{label_text} is listed for this verified role.",
                    "verified role technology", listed=True,
                ))
    if not direct and not listed:
        raise ValueError("No matchable technical evidence found in the private candidate facts.")
    candidate = _record(facts.get("candidate"))
    sponsorship = normalized(_text(candidate.get("sponsorship_requirement")))
    return Candidate(
        direct, listed,
        tuple(_record(row) for row in _list(facts.get("education"))),
        tuple(_record(row) for row in _list(facts.get("languages"))),
        candidate.get("sponsorship_required") is True or bool(
            re.search(r"\b(?:requires?|needs?) (?:[a-z]+ ){0,2}(?:sponsorship|sponsor)\b", sponsorship)
        ),
    )


def clauses(description: str):
    """Yield posting text with a priority only where there is a clear cue."""
    heading = None
    start = 0
    for found in HEADINGS.finditer(description):
        yield from _section(description[start:found.start()], heading)
        heading = found.lastgroup
        start = found.end()
    yield from _section(description[start:], heading)


def _section(text, heading):
    for clause in CLAUSES.split(text):
        clause = clause.strip(" \t:-")
        if not clause or heading == "ignore":
            continue
        if PREFERRED.search(clause):
            priority = "preferred"
        elif REQUIRED.search(clause):
            priority = "required"
        elif heading:
            priority = heading
        elif WORK.search(clause):
            priority = "responsibility"
        else:
            continue
        yield clause, priority


def extract_criteria(description: str) -> list[Criterion]:
    found = {}
    for clause, priority in clauses(description):
        if NEGATED.search(clause):
            continue
        text = normalized(clause)
        mentions = sorted(
            (hit.start(), hit.end(), label)
            for label, pattern in PATTERNS.items()
            if (hit := pattern.search(text))
        )
        previous_end = 0
        groups = []
        for start, end, label in mentions:
            if groups and start < previous_end:
                continue
            between = text[previous_end:start].strip()
            if groups and between in {"or", "ou"}:
                groups[-1].append(label)
            else:
                groups.append([label])
            previous_end = end
        for group in groups:
            key = tuple(sorted(group))
            if key not in found or WEIGHTS[priority] > WEIGHTS[found[key].priority]:
                # Long flattened descriptions can contain several sentences in a section.
                # Keep context around the first relevant term when possible.
                excerpt = _excerpt(clause, group[0])
                found[key] = Criterion(tuple(group), priority, excerpt)
    return sorted(found.values(), key=lambda item: -WEIGHTS[item.priority])


def _excerpt(text, label):
    if len(text) <= 340:
        return text
    hit = PATTERNS[label].search(normalized(text))
    start = max(0, (hit.start() if hit else 0) - 100)
    return ("…" if start else "") + text[start:start + 340] + ("…" if start + 340 < len(text) else "")


def match_criteria(criteria: list[Criterion], candidate: Candidate) -> tuple[int | None, list[dict]]:
    results = []
    points = 0
    possible = 0
    for criterion in criteria:
        weight = WEIGHTS[criterion.priority]
        possible += weight
        direct = next((candidate.evidence[skill] for skill in criterion.skills if skill in candidate.evidence), None)
        listed = next((candidate.listed[skill] for skill in criterion.skills if skill in candidate.listed), None)
        related = next((
            (skill, candidate.evidence[skill])
            for family in FAMILIES if family.intersection(criterion.skills)
            for skill in sorted(family) if skill in candidate.evidence
        ), None)
        if direct:
            status, proof, support, credit = "direct", direct, None, 1.0
        elif listed:
            status, proof, support, credit = "listed", listed, None, 0.6
        elif related:
            status, proof, support, credit = "transferable", related[1], related[0], 0.35
        else:
            status, proof, support, credit = "gap", None, None, 0.0
        points += weight * credit
        results.append({
            "criterion": " or ".join(criterion.skills), "priority": criterion.priority,
            "posting_excerpt": criterion.excerpt, "match": status, "related_skill": support,
            "evidence_id": proof.id if proof else None,
            "evidence": proof.statement if proof else None, "scope": proof.scope if proof else None,
        })
    return (round(points / possible * 100) if possible else None), results
