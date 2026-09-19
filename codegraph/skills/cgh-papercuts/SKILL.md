---
name: cgh-papercuts
description: Log and reuse "papercuts", the tooling and environment time-sinks that slow a session down, through cgh. Read them FIRST when a tool fails mysteriously (a wedged process, an env gotcha, a flag that misbehaves, a CI job stuck); log one when you lose time and find the fix, so the next session does not re-pay it. Triggers when tooling or the environment fails in a way that is not the code you are writing.
---

# Papercuts: don't re-pay the same lost time

A **papercut** is anything outside the code you are writing that quietly costs
time: a tool that fails mysteriously, a wedged background process, a flag that
behaves differently than its docs, an environment or auth gotcha, a CI job
stuck for no obvious reason, a slow path that had a fast one. cgh keeps them in
this repo's **knowledge** store (kind `gotcha`, tag `papercut`), so they are
searchable and they resurface in the resume bundle at the next session.

## Read FIRST when tooling fails mysteriously

Before starting a fresh investigation into a tool or environment failure, check
whether someone already paid for the answer:

```bash
cgh papercut <symptom>     # search this repo's papercuts
cgh papercut               # list them, newest first
```

Or from an MCP client: `knowledge_search("papercut <symptom>")`. Grep the
symptom, not your guess about the cause. This is the whole point of the store.

## Log one when you lose time and find the fix

Once you have spent more than a couple of minutes on a tooling or environment
failure and know the fix, record it before moving on, while it is fresh:

```bash
cgh papercut add "<symptom: what you would grep at 2am>" --fix "<the exact command or change that worked>"
```

Or from an MCP client:
`knowledge_record(title="Papercut: <symptom>", body="Symptom ... Cause ... Fix ... Check ...", kind="gotcha", tags="papercut, <tool>")`.

Write the **symptom** as the string a future session would search for, and the
**fix** as the exact command or edit, not a description of it. A tag naming the
tool (`gcloud`, `duckdb`, `actions`, ...) makes it findable.

## What is NOT a papercut

Ordinary bugs in the feature you are building. Those belong to the tracker or
to `knowledge_record` as a plain `gotcha`. Papercuts are specifically about the
**tooling and environment** getting in the way of the work, not the work
itself.
