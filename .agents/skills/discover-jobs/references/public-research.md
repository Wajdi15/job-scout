# Public research for Job Scout

Search by role and location, then verify the official listing. Rotate accurate
aliases such as AI engineer, computer vision engineer, Python backend engineer,
full-stack developer, research engineer, and funded PhD. Try translated titles
when useful, such as ingénieur de recherche or ingénieur vision par ordinateur.
Use generic region terms such as Europe when none are supplied.

| Source key | Public pages to search |
| --- | --- |
| `workable` | [Workable-hosted jobs](https://apply.workable.com/) on `apply.workable.com`. |
| `smartrecruiters` | Employer postings on `jobs.smartrecruiters.com`. The app can also poll configured employer boards. |
| `euraxess` | [EURAXESS jobs and funding](https://euraxess.ec.europa.eu/jobs/search). |
| `inria` | [Inria job listings](https://jobs.inria.fr/public/classic/en/offres). |
| `fraunhofer` | [Fraunhofer careers](https://jobs.fraunhofer.de/). |
| `academictransfer` | [AcademicTransfer jobs](https://www.academictransfer.com/en/jobs/). |
| `ashby` | Employer postings on `jobs.ashbyhq.com`. |

Check each site during a full run where tools allow. Search indexing and page
access vary. Use an institution's original posting when you can verify it and
it belongs to one of the supported sites. Avoid adding the same role from an
aggregator and its official mirror. Save factual metadata and a short
paraphrase only. Leave unstated sponsorship, funding, dates, and requirements
unknown.

Use several targeted searches across role families and regions. For deeper
coverage, use the public board's own filters or check an employer's official
careers page for links to individual postings. If a relevant employer uses
Greenhouse, Lever, or SmartRecruiters, its verified public board identifier can
also extend the private API watchlist. Keep real watchlists outside Git.

Write a private JSON bundle with the structure below. `checked_at` is the
actual recent check time in ISO format with a timezone. In `coverage`, use
`ok` for a search you completed, `partial` for incomplete verification, and
`error` when a source could not be checked. Omit a source you did not attempt.
Every posting's source must appear in coverage. An `error` source has no
postings. Set `open` only after checking the original job page and any stated
deadline. Use ISO dates for optional `deadline` and `published_at`. The
optional `location`, `workplace`, and `role_type` fields must be public facts.

```json
{
  "version": 1,
  "checked_at": "2026-01-01T12:00:00+00:00",
  "coverage": {"inria": "ok", "workable": "partial"},
  "postings": [
    {
      "source": "inria",
      "url": "https://jobs.inria.fr/public/classic/en/offres/2026-00001",
      "title": "Fictional Research Engineer",
      "employer": "Fictional example",
      "open": true,
      "summary": "Requirements: Experience with Python and automated testing. Responsibilities: Maintain research data pipelines."
    }
  ]
}
```

That example is fictional. For a selected web-only job, use a separate fresh
bundle (usually one posting) checked after the ranking report was generated.
Call `select-job` with `--research-verification` and the bundle's private
absolute path.
