# Evidence policy

| Status | Use |
|---|---|
| VERIFIED | You inspected a checkable source and it supports the stated claim. |
| REPORTED | Someone explicitly reported the claim. Attribute the report. |
| UNVERIFIED | Evidence is absent or insufficient. |
| CONFLICTED | Sources or active claims disagree. Preserve both sides. |
| STALE | A previous basis changed or cannot be confirmed. |

Use `kind` and a precise `locator` for each source. Supported kinds are `user`, `session`, `file`, `test`, `git_commit`, `report`, and `task`. A file or test reference requires `path`. For a checkable anchor, include `sha256`, and add a one-based `line` if meaningful. A Git reference requires `commit` and is automatically checked against the 20 most recent commits reachable from HEAD. Report and task locators are provenance text and the CLI never follows URLs.

A test reference requires the observed `exit_code`. For VERIFIED DONE, retain a sanitized project-local test log, and provide its SHA-256, a timezone-aware `observed_at`, the relevant command and result in `locator`, and exit code 0. Test exit values are supplied by the agent or operator. The CLI checks the referenced bytes and metadata. It cannot establish that a log is genuine, that the check exercised the stated behavior, or that a cited commit proves developer intent. Recheck before assigning VERIFIED. Do not fabricate an observation or use a future plan as completion evidence.
