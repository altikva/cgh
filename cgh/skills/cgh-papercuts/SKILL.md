---
name: cgh-papercuts
description: "Read, then record, tooling or environment failures that cost time and are not the code you are writing. BEFORE investigating, search papercuts for the symptom; AFTER finding the fix, record it. Triggers: a wedged process or held lock ('database is locked', 'resource busy'), 'SERVICE_DISABLED' or 'permission denied', a CI job stuck queued, a push that never ran, a stale token while another works, a flag unlike its docs, a reinstall that broke a daemon."
---

# Papercuts: read before you dig, record after you bleed

A **papercut** is anything outside the code you are writing that quietly costs
time: a wedged process, a lock nobody released, an auth gotcha, a CI job stuck
for no obvious reason, a flag that behaves unlike its docs, a reinstall that
broke a resident daemon. This repo keeps them in its **knowledge** store so the
next session (yours or another) does not re-pay the same lost time. Treat the
two moves below as reflexes, not chores you do when reminded.

## 1. READ FIRST, before investigating a tooling failure

The instant a tool or the environment fails in a way that is not your code,
search the papercuts before forming a theory:

- **Primary (you have MCP): `knowledge_search("papercut <symptom>")`.**
- Filter to entries whose `tags` contain `papercut`. Grep the *symptom string*,
  not your guess at the cause.

Someone probably already paid for this answer. Only start a fresh investigation
if the search comes back empty.

## 2. RECORD, after you lose time and find the fix

Once you have spent more than a couple of minutes on a tooling or environment
failure and know the fix, record it immediately, while it is fresh, without
waiting to be asked:

- **Primary (you have MCP):**
  ```
  knowledge_record(
    title="Papercut: <symptom>",
    body="Symptom: <what you would grep at 2am>\nCause: <root cause>\nFix: <the exact command or change that worked>\nCheck: <how to confirm it worked>",
    kind="gotcha",
    tags="papercut, <tool>",
  )
  ```
- **Backup (no MCP available, e.g. the server is disconnected):**
  ```
  cgh papercut add "<symptom>" --fix "<exact fix>"
  ```

Write the **symptom** as the string a future session would search for, the
**fix** as the exact command or edit (not a description of it), and tag the tool
(`gcloud`, `duckdb`, `actions`, `npm`, ...) so it is findable.

## The `cgh papercut` command is for the human

The user has no direct access to the knowledge store, so `cgh papercut` (list)
and `cgh papercut <query>` (search) exist so THEY can read what has been logged
from a terminal. As an agent, prefer the MCP tools above for both reading and
writing; reach for `cgh papercut add` only as the no-MCP backup.

## What is NOT a papercut

Ordinary bugs in the feature you are building. Those go to the tracker, or to
`knowledge_record` as a plain `gotcha` without the `papercut` tag. Papercuts are
specifically the **tooling and environment** getting in the way of the work.
