# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The pure, I/O-free core of code generation. build_prompt turns
#              a spec plus reference files into a (system, user) pair;
#              extract_code pulls the code out of a model reply, dropping any
#              surrounding prose so a cheap model's apology or half-answer
#              never reaches disk; generate_code runs a Backend and returns
#              the cleaned result. No file writes, no network, no gate here:
#              those wrap this. A Backend is injectable, so FakeBackend is the
#              whole test harness.

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# The system instruction. It asks for the file inside a single fenced block
# on purpose: the fence is the frame that lets extract_code keep the code and
# discard any prose a cheap model adds despite instructions. A reply with no
# fence is treated as "nothing usable" rather than written to disk as-is.
SYSTEM_PROMPT = (
    "You generate one code file from a spec and one or more reference files. "
    "Match the reference's conventions, naming, structure and style exactly. "
    "Return the file's complete contents as a single fenced code block "
    "(```) and nothing outside the fence. If you cannot produce the file, "
    "return an empty fenced block."
)

_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


class GenerationError(RuntimeError):
    """Raised when a backend returns nothing usable."""


@dataclass(frozen=True, slots=True)
class GenResult:
    """A backend's reply after cleaning. ``cost`` is the backend's own
    estimate in whatever unit it reports (0.0 for local backends)."""

    code: str
    cost: float = 0.0
    backend: str = ""


@runtime_checkable
class Backend(Protocol):
    """A code-generating model. One method, so FakeBackend is trivial and
    the real backends (agent CLI, local, cloud) drop in behind the same
    seam later. ``is_local`` is True when generation never leaves the
    machine (e.g. a local model); the flow skips the egress gate for those,
    exactly like the rest of cgh."""

    name: str
    is_local: bool

    def generate(self, system: str, user: str) -> tuple[str, float]:
        """Return (raw_text, cost). Must not raise for an empty reply,
        return ("", cost) instead so the caller decides."""
        ...


def build_prompt(spec: str, references: list[tuple[str, str]]) -> tuple[str, str]:
    """Build the (system, user) prompt. ``references`` is a list of
    (path, text). The path is labeled so the model sees which conventions to
    mirror; the spec leads so it is not buried under a long reference."""
    blocks = [f"SPEC:\n{spec.strip()}\n"]
    for path, text in references:
        blocks.append(f'<reference path="{path}">\n{text}\n</reference>')
    return SYSTEM_PROMPT, "\n\n".join(blocks)


def extract_code(reply: str) -> str:
    """Pull the code out of a model reply.

    Only the first fenced code block counts: its contents are the file, and
    any prose the model added around the fence is dropped. A reply with no
    fence returns the empty string, which the caller reads as "nothing
    usable" and refuses to write. This is deliberate: it is safer to refuse
    an unframed reply than to write a cheap model's apology or half-answer
    over a target file.
    """
    m = _FENCE_RE.search(reply)
    if m:
        return m.group(1).strip("\n")
    return ""


def generate_code(
    spec: str,
    references: list[tuple[str, str]],
    backend: Backend,
) -> GenResult:
    """Run one generation. Pure orchestration: build the prompt, call the
    backend, clean the reply. No file is written and no egress gate is
    consulted here; that is the caller's job. Raises GenerationError when the
    backend returns nothing usable so an empty file is never produced."""
    if not spec.strip():
        raise GenerationError("empty spec")
    system, user = build_prompt(spec, references)
    raw, cost = backend.generate(system, user)
    code = extract_code(raw)
    if not code.strip():
        raise GenerationError(f"backend {backend.name!r} returned no code")
    return GenResult(code=code, cost=cost, backend=backend.name)
