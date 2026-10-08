# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The "which tool answers which question" guide, shared by the
#              MCP server instructions and the usage rules installed for each
#              agent. Tool names are written bare (no mcp__codegraph__
#              prefix) because each agent spells the MCP prefix differently.

from __future__ import annotations

SEARCH_GUIDE = """\
Which cgh tool answers which question. Questions about the CODE go to the
code tools; knowledge_search never searches the code.

About the code:
  - Where is X defined, what is X ........ symbol_lookup(name)
  - A name you only half know ............ search_symbols(query)
  - Code about a concept, in words ....... fts_search(query)
      a sentence is fine, French or English ("relance des webhooks en
      echec"): it matches symbol names and docstrings
  - Starting a task, broad question ...... context_for_task(task)
  - Who calls X / what X calls ........... find_callers(name) / find_callees(name)
  - Every place a string or regex occurs . pattern_search(pattern, glob?)
  - Files around a feature keyword ....... domain_map(keyword)
  - What a file holds, before reading it . file_summary(path)
  - Markdown docs ........................ search_docs(query)
  - What breaks / which tests to run ..... impact_report(files), tests_for(path)

Notes saved by agents and people, NOT code:
  - knowledge_search(query) / knowledge_list(): decisions, gotchas,
    papercuts recorded in earlier sessions. Use it to check whether a
    problem was already solved. An empty result says nothing about the code.
  - memory_search / plan_search: the user's agent memory and plan files.
  - resume(): once at session start, to reload the last session.
"""
