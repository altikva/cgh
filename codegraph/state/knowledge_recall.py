# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-23
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Free-text recall for the knowledge store. Agents search with a
#              sentence describing a symptom ("le verrou est périmé après un
#              timeout") while the entry was recorded with other words ("stale
#              lock after the delay"). This module turns such a sentence into
#              concepts: words folded to lower-case ASCII, stopwords dropped,
#              each word cut to a light stem and widened with its French and
#              English synonyms from SYNONYMS. It builds the FTS5 expression
#              that ORs the concepts, measures how many concepts an entry
#              covers to give it a confidence (high, medium, low), finds close
#              spellings when nothing matches, and drafts the knowledge_record
#              call that would make the next search succeed. Pure functions:
#              the SQL lives in call_log.

from __future__ import annotations

import difflib
import re
import time
import unicodedata
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass

# Words with the same meaning, French and English mixed, folded (no accents,
# lower case). A query word joins a group when its stem equals the stem of a
# member. Add a word to a group, or a new tuple, to teach recall a synonym.
SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("lock", "locked", "verrou", "verrouillage", "verrouille", "lockfile"),
    ("stale", "perime", "obsolete", "outdated", "expired", "expire", "drift"),
    ("token", "jeton", "credential", "credentials"),
    ("timeout", "delai", "timedout"),
    ("stuck", "hang", "hung", "wedged", "bloque", "coince", "fige", "frozen", "freeze"),
    ("error", "erreur", "crash", "plantage", "exception"),
    ("fail", "failure", "failed", "echec", "echoue"),
    ("slow", "lent", "lenteur", "latency", "latence"),
    ("index", "indexing", "reindex", "indexation", "indexer"),
    ("search", "recherche", "chercher", "lookup", "find", "trouver"),
    ("database", "db", "bdd"),
    ("missing", "manquant", "absent", "empty", "vide", "introuvable"),
    ("permission", "droit", "denied", "refuse", "interdit", "forbidden"),
    ("install", "installation", "installer"),
    ("upgrade", "update", "maj", "bump"),
    ("delete", "remove", "supprimer", "suppression", "effacer"),
    ("config", "configuration", "setting", "settings", "parametre", "reglage"),
    ("branch", "branche"),
    ("release", "publish", "publier", "publication"),
    ("process", "processus", "daemon", "demon"),
    ("kill", "tuer", "arreter", "stop"),
    ("restart", "redemarrer", "relancer", "relance", "retry"),
    ("file", "fichier"),
    ("folder", "directory", "dossier", "repertoire"),
    ("network", "reseau"),
    ("auth", "authentication", "authentification", "login"),
    ("connection", "connexion", "connect"),
    ("memory", "memoire"),
    ("warning", "avertissement", "alerte"),
    ("duplicate", "doublon", "duplique"),
    ("rename", "renommer"),
    ("encoding", "encodage"),
    ("path", "chemin"),
    ("queue", "queued", "attente"),
    ("build", "compile", "compilation"),
    ("deploy", "deploiement", "deployer"),
    ("hook", "hooks"),
    ("owner", "proprietaire"),
    ("worker", "travailleur"),
    ("watcher", "surveillance"),
    ("test", "tests", "pytest"),
)


def _wordset(*chunks: str) -> frozenset[str]:
    return frozenset(w for chunk in chunks for w in chunk.split())


STOPWORDS: frozenset[str] = _wordset(
    # English
    "a an and or of to in on at for with without from by is are was were be "
    "been being it its this that these those when while after before during "
    "i we you they he she my our your their me us not no do does did done has "
    "have had can could cannot should would will just also so then than but "
    "if as into about over under again what which who why how where there here "
    "all any some each every via vs etc get got gets use using used one very "
    "more most less only still even keeps keep why again now",
    # French
    "le la les un une des du de d l et ou au aux en dans sur sous pour par "
    "avec sans ce cet cette ces qui que quoi dont est sont etait etre ete il "
    "elle ils elles on nous vous je tu me te se ne pas plus moins tres aussi "
    "mais donc car si quand lors apres avant pendant comme son sa ses leur "
    "leurs mon ma mes notre nos votre vos y lui tout tous toute toutes fait "
    "faire alors encore deja meme qu c n s j m t ca rien jamais toujours",
)

# Words that say what kind of note is wanted rather than what it is about.
# They stay in the FTS expression but only count toward confidence when the
# query holds nothing else.
GENERIC: frozenset[str] = frozenset(
    {"papercut", "gotcha", "cgh", "codegraph", "issue", "problem", "probleme", "bug"}
)

# Suffixes a light stemmer strips, longest first, French and English mixed.
_SUFFIXES: tuple[str, ...] = tuple(
    sorted(
        (
            "ations", "ation", "ements", "ement", "ments", "ment", "ings", "ing",
            "ages", "age", "ions", "ion", "euses", "euse", "eurs", "eur", "ers",
            "er", "ees", "ee", "ies", "ied", "es", "ed", "ez", "ly", "s", "e",
        ),
        key=len,
        reverse=True,
    )
)  # fmt: skip
_MIN_STEM = 4
# A stem shorter than this is matched as a whole word, not as a prefix.
_MIN_PREFIX = 3

CONFIDENCE_ORDER: tuple[str, ...] = ("high", "medium", "low")

_WORD = re.compile(r"[a-z0-9_]+")


def fold(text: str) -> str:
    """Lower-case ASCII form of ``text``: accents removed, case folded."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def words(text: str) -> list[str]:
    """The folded words of ``text``, in order, stopwords and one-letter
    words dropped."""
    return [w for w in _WORD.findall(fold(text)) if len(w) > 1 and w not in STOPWORDS]


def stem(word: str) -> str:
    """A light French and English stem: the longest known suffix whose
    removal leaves at least four characters."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def _build_synonym_index(
    groups: Iterable[tuple[str, ...]],
) -> dict[str, frozenset[str]]:
    index: dict[str, set[str]] = {}
    for group in groups:
        stems = {stem(fold(w)) for w in group}
        for s in stems:
            index.setdefault(s, set()).update(stems)
    return {k: frozenset(v) for k, v in index.items()}


_SYNONYM_INDEX = _build_synonym_index(SYNONYMS)


@dataclass(frozen=True, slots=True)
class Concept:
    """One query word and the stems that count as a match for it."""

    word: str
    stems: frozenset[str]
    generic: bool = False


def concepts(query: str) -> list[Concept]:
    """The query's concepts, one per distinct stem, synonyms included."""
    out: list[Concept] = []
    seen: set[str] = set()
    for w in words(query):
        s = stem(w)
        if s in seen:
            continue
        seen.add(s)
        stems = _SYNONYM_INDEX.get(s, frozenset()) | {s}
        out.append(Concept(word=w, stems=frozenset(stems), generic=w in GENERIC))
    return out


def _term(s: str) -> str:
    # Stems hold [a-z0-9_] only, so quoting cannot break the expression.
    return f'"{s}"*' if len(s) >= _MIN_PREFIX else f'"{s}"'


def fts_expression(items: list[Concept]) -> str:
    """An FTS5 MATCH expression that ORs every stem of every concept. Empty
    when there is nothing to search for."""
    terms = sorted({_term(s) for c in items for s in c.stems})
    return " OR ".join(terms)


class _Vocab:
    """The folded words of one entry, sorted for prefix lookups."""

    __slots__ = ("_set", "_sorted")

    def __init__(self, text: str) -> None:
        self._set = set(_WORD.findall(fold(text)))
        self._sorted = sorted(self._set)

    def has(self, s: str) -> bool:
        if len(s) < _MIN_PREFIX:
            return s in self._set
        i = bisect_left(self._sorted, s)
        return i < len(self._sorted) and self._sorted[i].startswith(s)


# A concept found only in the body counts for this share of one found in the
# title or the tags: a long body mentions many words in passing.
BODY_WEIGHT = 0.6


def coverage(items: list[Concept], head: str, body: str = "") -> tuple[float, int]:
    """(weighted concepts covered, concepts that count). A concept in
    ``head`` (title and tags) counts 1, one only in ``body`` counts
    BODY_WEIGHT. Generic words count only when the query holds nothing
    else."""
    counted = [c for c in items if not c.generic] or items
    top, rest = _Vocab(head), _Vocab(body)
    matched = 0.0
    for c in counted:
        if any(top.has(s) for s in c.stems):
            matched += 1.0
        elif any(rest.has(s) for s in c.stems):
            matched += BODY_WEIGHT
    return round(matched, 4), len(counted)


def confidence(matched: float, counted: int) -> str | None:
    """high, medium or low from the share of concepts covered; None when
    nothing is covered."""
    if matched <= 0 or counted <= 0:
        return None
    ratio = matched / counted
    if ratio >= 0.66:
        return "high"
    if ratio >= 0.34:
        return "medium"
    return "low"


def close_words(items: list[Concept], vocabulary: Iterable[str]) -> list[str]:
    """Store words spelled close to a query word (a typo, a plural the
    stemmer missed), for a last-resort search."""
    vocab = [w for w in set(vocabulary) if len(w) >= 4]
    found: list[str] = []
    for c in items:
        if c.generic or len(c.word) < 5:
            continue
        for w in difflib.get_close_matches(c.word, vocab, n=2, cutoff=0.8):
            if w not in found:
                found.append(w)
    return found


def suggest_record(query: str, kind: str | None = None) -> dict:
    """The knowledge_record call that would make this query succeed next
    time: tags drawn from the query's own words, generic words left out."""
    items = concepts(query)
    folded = [c.word for c in items]
    tags: list[str] = []
    if "papercut" in folded:
        tags.append("papercut")
    for c in items:
        if c.generic or c.word.isdigit():
            continue
        if c.word not in tags:
            tags.append(c.word)
        if len(tags) >= 5:
            break
    suggested_kind = kind or ("gotcha" if "papercut" in folded else "note")
    return {
        "tool": "knowledge_record",
        "kind": suggested_kind,
        "tags": ",".join(tags),
        "why": (
            "Nothing recorded matches. Once this is solved, record it with "
            "these tags so the next session finds it."
        ),
    }


# How much of each hit a search answer carries. The first hit keeps its body
# up to FIRST_BODY characters; the others keep their title, tags, confidence
# and the first BRIEF_BODY characters, the rest being one call away.
FIRST_BODY = 1500
BRIEF_BODY = 200
DEFAULT_LIMIT = 5
# knowledge_list answers with briefs only, so a page holds more entries.
LIST_DEFAULT_LIMIT = 10


def clip(text: str, limit: int) -> tuple[str, bool]:
    """``text`` cut to at most ``limit`` characters, at a line end when one
    is in the second half, else at a word end. Returns (text, clipped)."""
    text = text or ""
    if len(text) <= limit:
        return text, False
    head = text[:limit]
    cut = head.rfind("\n")
    if cut < limit // 2:
        cut = head.rfind(" ")
    if cut >= limit // 2:
        head = head[:cut]
    return head.rstrip() + " [...]", True


def shape_hits(hits: list[dict]) -> bool:
    """Trim search hits in place to what an answer needs: the first keeps
    every field with its body clipped to FIRST_BODY, the others become a
    brief (id, title, kind, tags, confidence, outcome status, scope, body
    head). Returns True when any body was clipped."""
    clipped_any = False
    for i, h in enumerate(hits):
        body, clipped = clip(
            str(h.get("body") or ""), FIRST_BODY if i == 0 else BRIEF_BODY
        )
        clipped_any |= clipped
        if i == 0:
            h["body"] = body
            if clipped:
                h["body_clipped"] = True
            continue
        brief: dict = {
            "id": h.get("id"),
            "title": h.get("title"),
            "kind": h.get("kind"),
            "tags": h.get("tags"),
            "confidence": h.get("confidence"),
            "body": body,
        }
        if clipped:
            brief["body_clipped"] = True
        if isinstance(h.get("outcome"), dict) and h["outcome"].get("status"):
            brief["outcome"] = {"status": h["outcome"]["status"]}
        if "scope" in h:
            brief["scope"] = h["scope"]
        h.clear()
        h.update(brief)
    return clipped_any


def list_brief(entry: dict) -> dict:
    """A knowledge_list row as a brief: id, title, kind, tags, when it was
    recorded (UTC, to the minute), the outcome status when rated, a
    ``promoted`` flag for an entry copied from another checkout, and the
    first BRIEF_BODY characters of the body. The whole entry is one
    knowledge_list(entry_id=...) call away."""
    body, clipped = clip(str(entry.get("body") or ""), BRIEF_BODY)
    brief: dict = {
        "id": entry.get("id"),
        "title": entry.get("title"),
        "kind": entry.get("kind"),
        "tags": entry.get("tags"),
    }
    try:
        ts = float(entry.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    if ts > 0:
        brief["recorded"] = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ts))
    outcome = entry.get("outcome")
    if isinstance(outcome, dict) and outcome.get("status"):
        brief["outcome"] = {"status": outcome["status"]}
    if entry.get("provenance"):
        brief["promoted"] = True
    brief["body"] = body
    if clipped:
        brief["body_clipped"] = True
    return brief
