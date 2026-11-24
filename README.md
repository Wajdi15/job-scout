# Job Scout

An open-source project for finding job openings, explaining the fit against verified experience, and preparing a reviewed résumé for a job you select.

Job Scout includes Codex roles, private search profiles, and public job feed collection. Scoring, scheduling, and résumé generation are planned.

## Set up privately

With Python 3.12 or newer, from the project root:

```sh
python -m pip install -r requirements.txt
python -m job_scout check-data-dir
python -m job_scout init-profiles
python -m job_scout check-profiles
```

The defaults are `%LOCALAPPDATA%\JobScout` on Windows and `~/.local/share/job-scout` elsewhere. Set `JOB_SCOUT_DATA_DIR` to an absolute path or pass `--data-dir` to change the location. Paths inside the public checkout are rejected, including symlinks into it.

To seed industry title suggestions from verified experience, pass `--candidate-facts` followed by the absolute path to a private candidate facts YAML file when running `init-profiles`. It uses experience entries with evidence to suggest titles and leaves that file untouched. You can edit or add any search title. If you have already created `search_preferences.yaml`, edit it in your private folder. Initialization preserves existing settings.

## Search profiles

See the [fictional example](examples/search-preferences.example.yaml) for both profiles and all supported fields.

- Set `required`, `preferred`, and `excluded` values per filter. Values within a list are alternatives. Required filters across different fields apply together. Preferred values guide ranking.
- Use `regions` for job location and `remote_regions` for countries where a remote employer allows you to work.
- Add aliases under the title they refer to. Aliases follow that title's filter level. Use `phd_funding` for PhD postings.
- Set `max_age_days` and `min_alert_score` to configure future discovery and alerts. The current commands create and validate settings.
- A missing posting fact, such as sponsorship, should remain unknown during matching. Search preferences guide discovery. Verified evidence and the selected posting guide the résumé.

## Collect job postings

Create `job_sources.yaml` next to your private search preferences. Use the
[example watchlist](examples/job-sources.example.yaml) as a starting point and
replace the fictional employer board identifiers.

- **Greenhouse:** use the employer board token from its board URL. [API documentation](https://docs.greenhouse.io/job-board.html)
- **Lever:** use the site name and set `instance` to `eu` for boards hosted at `jobs.eu.lever.co`. [API documentation](https://github.com/lever/postings-api)
- **Research:** `provider: bath` with `board: research` uses the university's Education & Research feed. [University of Bath RSS](https://www.bath.ac.uk/jobs/rss/)

Run `python -m job_scout collect-jobs`. The command only sends public board
identifiers to fixed HTTPS endpoints. It stores job data and source-check
history in `jobs.sqlite` in private storage. Repeated results update the
existing job and preserve its first-seen date. Publication dates and deadlines
stay empty when the source omits them.

Each source reports a count and a status. A failed source does not interrupt
the other checks, and a partial or failed run exits with status 1. A source
that returned zero postings can still report success. The watchlist controls
collection. Profile-based ranking, alerts, and submission preparation are
separate future steps.

## Test

Run `python -m unittest discover -s tests -v`. The tests use fictional data.

[Report a security issue privately](SECURITY.md).
