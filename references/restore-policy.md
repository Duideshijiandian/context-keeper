# Restore policy

Start with `normal` unless the user requested another depth. Summarize the current goal first. If a dated goal record and the original profile differ, report the basis for the current goal. Do not invent content for absent sections.

- **SHORT** selects the smallest useful set of complete records and at most two proposed steps.
- **NORMAL** includes the profile, major active records, the reported handoff, and next steps.
- **DETAILED** adds selected evidence and recent archive entries, within a larger budget.

The script prioritizes by section, declared importance and current confidence. It selects whole records, reports omitted counts, and preserves record IDs for further retrieval. Budgets limit selected content rather than promising host token counts. Use `restore --record ID` for evidence and `restore --record ID --at CAPTURE_UUID` for a past version. Retired decisions remain retrievable.

## One task at a time

On default restore, present `resumable_tasks` as the task selection list. Each entry gives `id`, `title`, `task_status` and `effective_status`. The list contains only current `task` records with status `IN_PROGRESS`, `TODO` or `BLOCKED`, in that order, newest update first within each status. Mark `BLOCKED` explicitly and show stale/conflicted evidence warnings alongside the task status. Completed, cancelled and superseded records are excluded. Membership does not depend on `next_steps`.

Present `tasks_needing_status` separately as status awaiting confirmation. These legacy tasks have no `task_status` (`null` in the index); ask for their status before treating them as resumable. Both compact indexes are complete across all budgets, outside the section-body budget and record limits. When `resumable_tasks` is empty, say there are no registered resumable tasks. Read the selected ID with `--task`; retain project context and completed results only as background when needed. `records_with_steps` remains an index of saved steps and may include completed tasks.

Use `restore --task SELECTOR` whenever the user asks about a single task in a project that holds several. SELECTOR resolves as an exact task or issue ID first, then as part of that record's title, case-insensitively. The answer is scoped to that record only: `task.content` is the task's summary, `next_steps` is that task's own recorded plan, and the project goal, the project note and every other record are deliberately left out so one task's handoff cannot leak into another's. `TASK_AMBIGUOUS` returns structured `candidates` — list them and let the user pick; `TASK_NOT_FOUND` means the name matched nothing, so list IDs from a `normal` restore instead of guessing. `next_steps_source: "missing"` marks a record captured before per-task steps existed: report the absence and read `content` for the stated next action rather than borrowing steps from another record. `--budget` is echoed but does not truncate this view. `--record` and `--task` cannot be combined.

Use `effective_status` for current trust. A stored VERIFIED claim can become STALE when its anchor or project baseline changes. Explicit conflicts take precedence in the view and both conditions may appear in validation. Describe historic or reported completion using its evidence confidence. Next steps are proposals. When the context is stale, confirm critical facts against current evidence before continuing work.

## Project view and per-task steps

The default view is project-level, and what the previous session left for the whole project arrives as one object, `project_note`:

- `project_note.summary` — that session's one-paragraph note about the project as a whole (`null` on `short`).
- `project_note.next_steps` — the project-level plan only. A record's own `next_steps` is never inlined here, and no bare `next_steps` key appears at the top level, so a reader never has to remember whose steps they are looking at.
- `project_note.scope` — `{limits, sources}`: what that session covered and what it was based on.

`records_with_steps` lists the IDs whose records carry their own steps, with `records_with_steps_count` for the full number (the list is capped at twelve). When summarizing a project that holds several tasks, report that index and say which tasks still have their own steps, instead of implying the project view shows everything; read a specific one with `restore --task ID`. `omitted_project_steps` counts only project-level steps dropped by the budget, so it stays zero even when records hold steps — never read it as "no steps exist".
