---
name: cgh-artifacts
description: MANDATORY reflex around files cgh cannot parse into its graph, the opaque ones you open with vision or an external extractor: PDFs, images (png/jpg/svg/...), and office documents (docx/xlsx/pptx). Reading them is expensive, so treat them as a cache. Do two things without being asked. (1) BEFORE inspecting such a file, check whether cgh already holds a summary of it (in Claude Code the Read hook surfaces it automatically; otherwise `cgh artifact recall <path>`). (2) AFTER you inspect one and understand it, record a concise summary so the next session, yours or another, does not re-pay the read. Concrete triggers: about to Read or view a .pdf/.png/.jpg/.jpeg/.gif/.webp/.svg/.docx/.xlsx/.pptx; an external tool (OCR, a document extractor, a vision model) just returned what a binary file contains; a screenshot or diagram you just interpreted. Do NOT wait to be asked; recalling and recording is part of doing the work.
---

# Artifacts: recall before you open, record after you read

cgh indexes code and Markdown into a graph, but it cannot see inside a **PDF, an
image, or an office document**. Those are opaque: the only way to know what a
`design.pdf` or a `screenshot.png` contains is to spend tokens opening it with
vision or an external extractor. That cost repeats every session unless someone
writes down what they found. This repo keeps those findings in its **knowledge**
store, keyed to the file and its content hash, so the read happens once. Treat
the two moves below as reflexes.

## 1. RECALL first, before inspecting an opaque file

The instant you are about to Read or view a file cgh can't parse (pdf, image,
office doc):

- **In Claude Code**, the Read pre-hook does this for you: if cgh has a saved
  summary it is injected before your Read, tagged with whether the file still
  matches the summary (fresh) or has changed since (stale). Use a fresh summary
  instead of re-reading; only re-inspect for detail beyond it, or when it is
  stale.
- **Otherwise (no hook / external agent):** `cgh artifact recall <path>` prints
  the saved summary and its freshness. `knowledge_search("artifact <name>")`
  from an MCP client does the same.

Someone probably already paid to read this file. Only open it fresh if nothing
is saved, or the saved summary is stale, or you need more than it captured.

## 2. RECORD, after you inspect one and understand it

Once you have opened an opaque file and know what it holds, save a concise
summary immediately, without waiting to be asked:

- **Primary (you have MCP):**
  ```
  knowledge_record(
    title="<file name>",
    body="<what the file is and the specifics worth not re-reading: the numbers on the chart, the fields on the form, what the screenshot shows>",
    kind="note",
    tags="artifact",
    file_refs=["<repo-relative path>"],
  )
  ```
- **Backup (no MCP available, e.g. the server is disconnected):**
  ```
  cgh artifact note <path> --summary "<the same concise summary>"
  ```
  The command hashes the file, so recall can later tell whether the summary
  still matches what is on disk.

Write the summary as what a future session would otherwise have to open the file
to learn: the content, not "a PDF about X". If the file later changes, its hash
stops matching and recall flags the summary stale, so record a new one then.

## The `cgh artifact` command is for the human, and for external agents

You (an MCP-connected agent) read and write through `knowledge_*`. The
`cgh artifact recall` / `list` commands exist so the **human**, who has no direct
access to the knowledge store, can read what has been saved from a terminal, and
so an **external** agent with no MCP or hook still has a recall/record surface.

## What is NOT an artifact here

Source code, Markdown, and plain-text data (CSV, JSON, config): cgh already
indexes or greps those cheaply, so they need no cache. This is specifically for
the binary and image files the graph cannot see into.
