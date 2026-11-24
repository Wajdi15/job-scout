# Job Scout

An open-source project for finding job openings, explaining the fit against verified experience, and preparing a reviewed résumé for a job you select.

This first commit contains the project scaffold, Codex agent and skill definitions, a fictional example, and a private data location check. Live discovery, scoring, scheduling, and résumé generation are planned.

## Try the foundation

With Python 3.12 or newer, from the project root:

```sh
python -m job_scout check-data-dir
python -m unittest discover -s tests -v
```

The first command validates the proposed location without reading or creating candidate files. Set `JOB_SCOUT_DATA_DIR` to an absolute path or pass `--data-dir` to override it. The defaults are `%LOCALAPPDATA%\JobScout` on Windows and `~/.local/share/job-scout` elsewhere. The check rejects directories inside this checkout, including links that resolve into it.

## Design

- Source collection and the scheduler will produce separate industry and research shortlists.
- Scouts work with public postings and search parameters. The matcher uses verified evidence and records eligibility separately from fit.
- A selected job goes through a private tailoring workflow and independent review. The candidate submits on the employer's site.
- Keep all real candidate facts, preferences, PDFs, application records, and credentials outside the checkout. The included example is fictional.

[Report a security issue privately](SECURITY.md).
