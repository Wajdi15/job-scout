"""Flag explicit eligibility conditions separately from technical fit."""

from datetime import date, datetime
import re

from .matching import Candidate, clauses, normalized
from .preferences import SearchProfile


SPONSOR_NO = re.compile(
    r"\b(?:no visa sponsorship|no sponsorship (?:is )?(?:available|offered|provided)|"
    r"(?:cannot|can not|unable to|will not|do not|does not) "
    r"(?:provide |offer |support )?(?:visa )?sponsor(?:ship)?|"
    r"(?:visa |work visa )?sponsorship (?:is )?not (?:available|offered|provided)|"
    r"must (?:[a-z]+ ){0,12}without (?:the need for |requiring )?(?:visa |employment )?sponsorship|"
    r"not eligible for (?:visa )?sponsorship|pas de parrainage de visa)\b"
)
SPONSOR_YES = re.compile(
    r"\b(?:visa sponsorship (?:is )?(?:available|provided|offered)|"
    r"(?:we|the employer) (?:will|can) (?:sponsor (?:a |your |work )?visa|"
    r"provide visa sponsorship|offer visa sponsorship)|sponsorship for (?:a |your )?work visa)\b"
)
QUALIFIED = re.compile(r"\b(?:may|might|could|eligible|subject to|depending|considered|where applicable|if eligible)\b")
EXISTING_AUTH = re.compile(
    r"\b(?:must (?:already )?(?:have|hold) (?:unrestricted |existing |a valid )?(?:right to work|work authorization|work permit)|"
    r"must (?:already )?(?:be legally authorized to work|be authorized to work|have the right to work)|"
    r"(?:citizenship|security clearance) (?:is )?required|must (?:already )?hold (?:an? )?(?:security clearance|citizenship))\b"
)
IS_PHD = re.compile(r"\b(?:phd|doctoral|doctorant|doctorante|doctorat)\b")
NOT_PHD = re.compile(r"\b(?:postdoc(?:toral)?|post doctoral|phd level)\b")
FUND_YES = re.compile(r"\b(?:fully funded|funded phd|phd studentship|doctoral studentship|financement doctoral)\b")
FUND_NO = re.compile(r"\b(?:unfunded|self funded|no funding|not funded|sans financement)\b")
LANGUAGES = {
    "english": "english", "anglais": "english", "french": "french", "francais": "french",
    "german": "german", "allemand": "german", "dutch": "dutch", "neerlandais": "dutch",
}
CEFR = ("a1", "a2", "b1", "b2", "c1", "c2")
LANGUAGE_LEVEL = re.compile(r"\b(?:a1|a2|b1|b2|c1|c2|native|fluent|courant|professional|bilingual|bilingue)\b")
LANGUAGE_NAME = re.compile(r"\b(?:" + "|".join(LANGUAGES) + r")\b")
DEGREES = {
    "doctorate": re.compile(r"\b(?:phd|doctorate|doctoral degree|doctorat)\b"),
    "master's": re.compile(r"\b(?:master(?: s)? degree|msc|m sc|master 2|bac 5)\b"),
    "bachelor's": re.compile(r"\b(?:bachelor(?: s)? degree|bsc|b sc|licence)\b"),
}
MIN_YEARS = re.compile(
    r"\b(?:at least |minimum (?:of )?)?(\d{1,2})\+? (?:years|ans|annees) (?:of |d )?(?:[\w ]{0,32})experience\b"
)


def finding(check, status, detail, posting_text=None, evidence_id=None):
    return {
        "check": check, "status": status, "detail": detail,
        "posting_excerpt": posting_text, "evidence_id": evidence_id,
    }


def phd_title(title):
    folded = normalized(title)
    return bool(IS_PHD.search(folded) and not NOT_PHD.search(folded))


def _polarity(text, positive, negative, *, conditional=True):
    negatives = list(negative.finditer(text))
    no = negatives[0] if negatives else None
    yes = next((positive_hit for positive_hit in positive.finditer(text)
                if not any(a.start() < positive_hit.end() and positive_hit.start() < a.end()
                           for a in negatives)), None)
    if yes and no:
        return "unknown", "The posting contains conflicting statements."
    if no:
        return "unavailable", f'Source text: “{text[no.start():no.end()]}”.'
    if yes:
        around = text[max(0, yes.start() - 30):yes.end() + 55]
        if conditional and QUALIFIED.search(around):
            return "unknown", "The positive statement appears conditional."
        return "available", f'Source text: “{text[yes.start():yes.end()]}”.'
    return "unknown", "The posting does not state this explicitly."


def _gate(name, state, detail, rule):
    if state == "unknown":
        status = "unknown"
    elif (rule.required and state not in rule.required) or state in rule.excluded:
        status = "unmet"
    else:
        status = "met"
    return finding(name, status, detail)


def _requirements(description, candidate):
    result = {}
    for clause, priority in clauses(description):
        if priority != "required":
            continue
        text = normalized(clause)
        for match in EXISTING_AUTH.finditer(text):
            result.setdefault("Work authorization or clearance", finding(
                "Work authorization or clearance", "unknown",
                "The posting asks for existing authorization, citizenship, or clearance. Verify the exact scope.",
                match.group(),
            ))
        degrees = [degree for degree, pattern in DEGREES.items() if pattern.search(text)]
        # Avoid mistaking a subject or an alternative qualification for a hard degree floor.
        if (len(degrees) == 1 and len(text) <= 450
                and re.search(r"\b(?:degree|msc|bsc|doctorate|doctorat)\b", text)
                and not re.search(r"\b(?:not required|no degree)\b", text)):
            degree = degrees[0]
            proof = next((row for row in candidate.degrees
                          if DEGREES[degree].search(normalized(str(row.get("degree", ""))))
                          and row.get("end") and normalized(str(row["end"])) not in {"present", "ongoing"}), None)
            status = "met" if proof else "unknown"
            result.setdefault(f"Degree: {degree}", finding(
                f"Degree level: {degree}", status,
                "Degree level is documented. Verify any required discipline or equivalence."
                if proof else "Required degree level or accepted equivalence needs verification.",
                clause[:340], str(proof.get("id", "")) if proof else None,
            ))
            if re.search(r"\b(?:degree|msc|bsc|doctorate|doctorat) (?:in|en|dans)\b", text):
                result.setdefault("Degree discipline", finding(
                    "Degree discipline", "unknown",
                    "Verify that the documented qualification is in an accepted discipline.",
                    clause[:340],
                ))
        for item in LANGUAGE_NAME.finditer(text):
            nearby = []
            for level in LANGUAGE_LEVEL.finditer(text):
                space = text[min(item.end(), level.end()):max(item.start(), level.start())]
                if len(space) <= 24 and not LANGUAGE_NAME.search(space) and not re.search(
                    r"\b(?:and|or|et|ou)\b", space
                ):
                    nearby.append((len(space), level))
            level = min(nearby, key=lambda pair: pair[0])[1] if nearby else None
            if level is None:
                continue
            language = LANGUAGES[item.group()]
            proof = next((row for row in candidate.languages
                          if LANGUAGES.get(normalized(str(row.get("language", "")))) == language), None)
            status = "unknown"
            if proof and level.group() in CEFR:
                documented = re.search(r"\b(?:a1|a2|b1|b2|c1|c2)\b", normalized(str(proof.get("level", ""))))
                if documented:
                    status = "met" if CEFR.index(documented.group()) >= CEFR.index(level.group()) else "unmet"
            result.setdefault(f"Language: {language}", finding(
                f"Language: {language}", status,
                f"Posting asks for {level.group().upper() if level.group() in CEFR else level.group()}. "
                + ("Verified level meets the stated minimum." if status == "met" else
                   "Verified level is below the stated minimum." if status == "unmet" else
                   "Verify how the documented proficiency maps to this requirement."),
                clause[:340], str(proof.get("id", "")) if proof else None,
            ))
        years = MIN_YEARS.search(text)
        if years:
            existing = result.get("Minimum experience")
            if not existing or int(years.group(1)) > existing["years"]:
                item = finding(
                    "Minimum experience", "unknown",
                    f"Posting asks for {years.group(1)} years. Verify the requested scope and count of relevant experience.",
                    years.group(),
                )
                item["years"] = int(years.group(1))
                result["Minimum experience"] = item
    return list(result.values())


def assess_eligibility(posting, profile: SearchProfile, candidate: Candidate, now: datetime):
    description = posting["description"]
    text = normalized(description)
    checks = []
    sponsor = profile.filters["sponsorship"]
    if candidate.needs_sponsorship or sponsor.required or sponsor.excluded or sponsor.preferred:
        state, detail = _polarity(text, SPONSOR_YES, SPONSOR_NO)
        if candidate.needs_sponsorship and state == "unavailable":
            checks.append(finding("Visa sponsorship", "unmet", detail))
        else:
            checks.append(_gate("Visa sponsorship", state, detail, sponsor))

    funding = profile.filters["phd_funding"]
    if phd_title(posting["title"]) and (funding.required or funding.excluded or funding.preferred):
        state, detail = _polarity(normalized(posting["title"] + " " + description), FUND_YES, FUND_NO)
        state = {"available": "funded", "unavailable": "unfunded"}.get(state, state)
        checks.append(_gate("PhD funding", state, detail, funding))

    if posting["deadline"]:
        try:
            value = posting["deadline"]
            expired = (date.fromisoformat(value) < now.date()) if len(value) == 10 else (
                datetime.fromisoformat(value).astimezone(now.tzinfo) < now
            )
        except ValueError:
            checks.append(finding("Application deadline", "unknown", "Verify the source deadline."))
        else:
            checks.append(finding("Application deadline", "unmet" if expired else "met", value))
    else:
        checks.append(finding("Application deadline", "unknown", "The feed did not publish a deadline."))
    checks.extend(_requirements(description, candidate))
    return checks


def eligibility_status(checks):
    states = {entry["status"] for entry in checks}
    return "unmet" if "unmet" in states else "unknown" if "unknown" in states or not states else "met"
