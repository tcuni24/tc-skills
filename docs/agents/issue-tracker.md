# Issue tracker: GitHub

Issues and specs live in GitHub Issues for `tcuni24/tc-skills`. Use the `gh` CLI from this repository, or pass `--repo tcuni24/tc-skills` explicitly.

## Conventions

- Read an issue and its discussion with `gh issue view <number> --comments`; fetch structured fields with `--json number,title,body,labels,comments` when needed.
- List issues with `gh issue list --state open --json number,title,body,labels,comments`, adding label and state filters for the task.
- Create an issue with `gh issue create --title "..." --body-file <body-file>`.
- Comment with `gh issue comment <number> --body-file <body-file>` when the user has authorized posting.
- Apply or remove labels with `gh issue edit <number> --add-label "..."` or `--remove-label "..."`. Use the mapping in `docs/agents/triage-labels.md`.
- Close an issue with `gh issue close <number>` when resolution is authorized.
- Store multiline bodies in a task-local file under the repository, or in `/project/tmp`; never use `/tmp`.

When a skill says to publish a spec, create a GitHub issue. If the task concerns an existing issue, keep that issue as the work item and publish the spec there instead of creating a duplicate.

When a skill says to fetch a ticket, read the issue body, labels, and comments.

If GitHub is unreachable, keep any draft local and report the blocker. Local drafts do not replace the tracker, and unpublished work must not be reported as published.

## Pull requests as a triage surface

**PRs as a request surface: no.**

Change this flag to `yes` only if external PRs should enter the issue triage workflow. When enabled, use `gh pr view`, `gh pr diff`, `gh pr list`, and the corresponding comment, label, and close commands. Include external authors with association `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE`; exclude `OWNER`, `MEMBER`, and `COLLABORATOR`.

GitHub shares issue and PR numbers. For an ambiguous reference, resolve its type before acting.

## Wayfinding operations

- A map is one issue labelled `wayfinder:map`, containing Notes, Decisions-so-far, and Fog.
- Tickets are child issues linked through GitHub sub-issues. If unavailable, use a task list in the map and a `Part of #<map>` line in each child. Ticket labels are `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or `wayfinder:task`.
- Record blockers through native issue dependencies. Use the blocker's numeric database ID, not its issue number or node ID. If dependencies are unavailable, use a `Blocked by: #<number>` line in the child.
- For the frontier, inspect open children in map order. Select the first unassigned child with no open blockers.
- Claim a ticket with `gh issue edit <number> --add-assignee @me`.
- Resolve by posting the answer, closing the ticket, and adding a gist of the decision plus its link to the map's Decisions-so-far section.
