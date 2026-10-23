# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-23
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Query understanding for the search tools. Agents often send a
#              sentence ("where are webhooks retried") where an identifier or
#              a regex is expected. This module tells the two apart, pulls
#              the likely identifiers out of a sentence, expands its words
#              through a small French / English synonym table, and ranks
#              results by usefulness: definitions before uses, files many
#              others import, then recently changed files. Everything here is
#              local and cheap: no network, no model, no extra index.

from __future__ import annotations

import os
import re
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codegraph.core.fts import STOPWORDS, fold_accents, prose_terms

# Synonyms for common code concepts, French and English. One tuple per
# concept: any word of a tuple found in a query brings in the others. Words
# are compared lowercased, without accents and after a light stemming, so a
# plural or a past participle finds its concept too. Add a concept as a new
# tuple; keep each word specific enough not to drag in unrelated code.
SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("retry", "retries", "backoff", "relance", "relancer", "réessayer", "rejouer"),
    ("webhook", "hook", "callback", "rappel"),
    ("size", "taille", "bytes", "octets", "length", "longueur", "budget"),
    ("compute", "calculate", "calcul", "calculer", "count", "compter", "measure"),
    ("response", "answer", "réponse", "reply", "payload", "résultat"),
    ("request", "requête", "demande"),
    ("cut", "truncate", "trim", "clip", "couper", "tronquer", "rogner"),
    ("limit", "cap", "ceiling", "max", "limite", "plafond"),
    ("error", "exception", "failure", "erreur", "échec", "panne"),
    ("auth", "authentication", "login", "authentification", "token", "jeton"),
    ("user", "account", "utilisateur", "compte"),
    ("delete", "remove", "purge", "supprimer", "suppression", "effacer"),
    ("create", "add", "insert", "créer", "création", "ajouter"),
    ("update", "modify", "edit", "modifier", "mise", "patch"),
    ("fetch", "read", "load", "lire", "récupérer", "charger"),
    ("save", "write", "store", "persist", "enregistrer", "écrire", "sauvegarder"),
    ("search", "find", "lookup", "query", "chercher", "rechercher", "trouver"),
    ("config", "configuration", "settings", "paramètres", "réglages"),
    ("cache", "memoize", "cached"),
    ("lock", "mutex", "verrou", "verrouiller"),
    ("spawn", "start", "launch", "démarrer", "lancer", "boot"),
    ("stop", "shutdown", "kill", "terminate", "arrêter", "arrêt"),
    ("owner", "propriétaire", "server", "serveur"),
    ("proxy", "relay", "bridge", "forward", "relais", "passerelle"),
    ("send", "emit", "publish", "dispatch", "notify", "envoyer", "envoi"),
    ("email", "mail", "courriel"),
    ("payment", "pay", "paiement", "payer", "billing", "facturation"),
    ("parse", "parser", "tokenize", "analyser"),
    ("index", "indexer", "indexing", "indexation", "scan", "reindex"),
    ("log", "logging", "logger", "journal", "journaliser"),
    ("permission", "role", "access", "acl", "droit", "accès", "rôle"),
    ("schedule", "cron", "job", "planifier", "tâche"),
    ("queue", "worker", "background", "arrière-plan"),
    ("validate", "validation", "verify", "check", "valider", "vérifier"),
    ("serialize", "format", "render", "dump", "sérialiser", "formater"),
    ("file", "path", "fichier", "chemin"),
    ("database", "db", "sql", "duckdb", "sqlite", "table"),
    ("connection", "connect", "conn", "connexion", "connecter"),
    ("session", "conversation"),
    ("download", "télécharger", "téléchargement"),
    ("upload", "téléverser", "téléversement"),
    ("timeout", "delay", "deadline", "délai", "expiration"),
    ("rank", "ranking", "score", "sort", "classement", "trier", "tri"),
    ("filter", "filtrer", "filtre", "exclude", "exclure"),
    ("import", "dependency", "dépendance", "importer"),
    ("call", "caller", "callee", "invoke", "appel", "appeler", "appelant"),
    ("dead", "unused", "mort", "inutilisé"),
    ("federation", "subrepo", "child", "fédération", "enfant"),
    ("memory", "mémoire", "souvenir"),
    ("test", "tests", "spec", "pytest"),
    ("hint", "suggestion", "advice", "conseil", "indice"),
    ("stale", "outdated", "obsolète", "périmé"),
)

# Words that say nothing about which code is meant once stopwords are gone:
# a question about "the function that handles X" is about X.
_FILLER = frozenset(
    fold_accents(w)
    for w in (
        "code",
        "function",
        "functions",
        "fonction",
        "fonctions",
        "method",
        "methods",
        "méthode",
        "classe",
        "symbol",
        "symbole",
        "defined",
        "définie",
        "défini",
        "used",
        "utilisé",
        "utilisée",
        "handled",
        "handle",
        "handles",
        "géré",
        "gérée",
        "gère",
        "implemented",
        "implémenté",
        "happens",
        "happen",
        "works",
        "work",
        "fonctionne",
        "marche",
        "logic",
        "logique",
        "place",
        "endroit",
        "thing",
        "chose",
        "done",
        "fait",
        "faite",
        "get",
        "gets",
        "make",
        "makes",
    )
)

# First words that open a line of code, never a question.
_CODE_KEYWORDS = frozenset(
    {
        "def",
        "class",
        "import",
        "from",
        "return",
        "async",
        "await",
        "function",
        "const",
        "let",
        "var",
        "export",
        "if",
        "elif",
        "else",
        "for",
        "while",
        "raise",
        "except",
        "try",
        "with",
        "self",
        "this",
        "func",
        "fn",
        "pub",
        "type",
        "interface",
        "resource",
        "module",
        "variable",
        "select",
        "package",
        "struct",
    }
)

# Literals that only show up in code.
_CODE_LITERALS = frozenset(
    {"None", "True", "False", "null", "nil", "undefined", "NULL", "true", "false"}
)

# Characters that make a query a regex or a code fragment. A trailing "?" is
# a question mark; "()" after a name is a call written in prose.
_REGEX_META = re.compile(r"[\\^$*+\[\]{}|()<>=;:#@~]")

_QUOTED = re.compile(r"[`\"“”«»]\s*([^`\"“”«»\s]{1,80})\s*[`\"“”«»]")
_SINGLE_QUOTED = re.compile(r"(?<![\w])'([A-Za-z_][\w.]*)'")
_DOTTED = re.compile(r"(?<![\w.])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_SNAKE = re.compile(r"(?<![\w.])_*[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+(?![\w.])")
_PRIVATE = re.compile(r"(?<![\w.])_+[A-Za-z][A-Za-z0-9]*(?![\w.])")
_CAMEL = re.compile(
    r"(?<![\w.])(?:[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+|[a-z]+[a-z0-9]*(?:[A-Z][a-z0-9]*)+)(?![\w.])"
)
_CALL = re.compile(r"\b([A-Za-z_]\w*)\(\)")
_NATURAL_WORD = re.compile(r"^[^\W\d_]+(?:['’-][^\W\d_]+)*[?.,!]?$")


def _stem(word: str) -> str:
    """A light French / English suffix strip, enough to match a plural or a
    participle to its concept: retried -> retry, calculee -> calcul."""
    w = word
    for suffix, repl in (
        ("ies", "y"),
        ("ied", "y"),
        ("ees", ""),
        ("ee", ""),
        ("es", ""),
        ("ing", ""),
        ("ed", ""),
        ("er", ""),
        ("e", ""),
        ("s", ""),
    ):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: len(w) - len(suffix)] + repl
    return w


def _keys(word: str) -> set[str]:
    folded = fold_accents(word.lower())
    return {folded, _stem(folded)}


def _build_index() -> dict[str, int]:
    index: dict[str, int] = {}
    for i, group in enumerate(SYNONYMS):
        for word in group:
            for key in _keys(word):
                index.setdefault(key, i)
    return index


_SYNONYM_INDEX = _build_index()


def concept_of(word: str) -> int | None:
    """The SYNONYMS row ``word`` belongs to, or None."""
    for key in _keys(word):
        if key in _SYNONYM_INDEX:
            return _SYNONYM_INDEX[key]
    return None


def synonyms_of(word: str) -> list[str]:
    """The other words of ``word``'s concept (empty when it has none)."""
    row = concept_of(word)
    if row is None:
        return []
    own = _keys(word)
    return [w for w in SYNONYMS[row] if not (_keys(w) & own)]


def looks_like_sentence(query: str) -> bool:
    """True when ``query`` reads as a question in words rather than an
    identifier, a regex or a line of code.

    The signals: at least two words, no regex or code punctuation (a
    trailing question mark and a ``name()`` call are fine), no code literal
    or keyword opening the line, mostly plain words, and either a stopword
    among them or a question mark at the end."""
    s = query.strip()
    if not s or len(s) > 300:
        return False
    body = _CALL.sub(r"\1", s).rstrip("?").rstrip()
    if _REGEX_META.search(body):
        return False
    words = body.split()
    if len(words) < 2:
        return False
    if words[0].lower() in _CODE_KEYWORDS or any(w in _CODE_LITERALS for w in words):
        return False
    natural = [w for w in words if _NATURAL_WORD.match(w)]
    if len(natural) / len(words) < 0.6:
        return False
    stop = sum(1 for w in natural if fold_accents(w.lower().strip("?.,!")) in STOPWORDS)
    if s.endswith("?"):
        return True
    return len(words) >= 3 and stop >= 1


def plain_words(query: str) -> int:
    """How many plain words ``query`` holds when it has no regex or code
    punctuation (0 otherwise): a weaker signal, used once a literal search
    came back empty."""
    body = _CALL.sub(r"\1", query.strip()).rstrip("?")
    if _REGEX_META.search(body):
        return 0
    return sum(1 for w in body.split() if _NATURAL_WORD.match(w))


def extract_identifiers(query: str) -> list[str]:
    """The identifiers a sentence names: backquoted or quoted words,
    dotted names, snake_case, _private, CamelCase and lowerCamel words and
    ``name()`` calls, in their order of appearance, deduplicated."""
    found: list[tuple[int, str]] = []
    for rx in (_QUOTED, _SINGLE_QUOTED, _CALL):
        for m in rx.finditer(query):
            found.append((m.start(1), m.group(1)))
    for rx in (_DOTTED, _SNAKE, _PRIVATE, _CAMEL):
        for m in rx.finditer(query):
            word = m.group(0)
            if rx is _DOTTED and any(len(p) < 2 for p in word.split(".")):
                continue  # "e.g", "i.e"
            found.append((m.start(), word))
    out: list[str] = []
    for _, word in sorted(found):
        word = word.strip(".")
        if word and word not in out and not any(word in o for o in out if "." in o):
            out.append(word)
    return out


@dataclass
class Intent:
    """What a query was understood as."""

    query: str
    sentence: bool
    identifiers: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    expanded: list[str] = field(default_factory=list)

    @property
    def interpreted_as(self) -> list[str]:
        return [*self.identifiers, *self.terms]

    @property
    def fts_text(self) -> str:
        """The free text handed to the FTS: identifiers, the meaningful
        words, then their synonyms."""
        return " ".join([*self.identifiers, *self.terms, *self.expanded])

    @property
    def concepts(self) -> list[str]:
        """One key per distinct idea of the query, identifiers included."""
        keys: list[str] = []
        for word in [*self.identifiers, *self.terms]:
            row = concept_of(word)
            key = f"#{row}" if row is not None else fold_accents(word.lower())
            if key not in keys:
                keys.append(key)
        return keys

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"interpreted_as": self.interpreted_as}
        if self.expanded:
            out["expanded"] = self.expanded
        return out


_MAX_EXPANDED = 16


def interpret(query: str, force: bool = False) -> Intent:
    """Understand ``query``. ``sentence`` says whether it reads as one
    (``force`` treats it so anyway); identifiers, terms and synonyms are
    filled in for a sentence only."""
    sentence = force or looks_like_sentence(query)
    if not sentence:
        return Intent(query=query, sentence=False)
    identifiers = extract_identifiers(query)
    rest = query
    for ident in identifiers:
        rest = rest.replace(ident, " ")
    terms = [
        t
        for t in prose_terms(rest, min_len=3)
        if fold_accents(t) not in _FILLER
        and not any(fold_accents(t) == fold_accents(i.lower()) for i in identifiers)
    ]
    expanded: list[str] = []
    seen = {fold_accents(w.lower()) for w in [*identifiers, *terms]}
    for term in terms:
        for syn in synonyms_of(term):
            key = fold_accents(syn.lower())
            if key not in seen and " " not in syn:
                seen.add(key)
                expanded.append(syn)
    return Intent(
        query=query,
        sentence=True,
        identifiers=identifiers,
        terms=terms,
        expanded=expanded[:_MAX_EXPANDED],
    )


_SPLIT_WORDS = re.compile(r"[^\W_]+", flags=re.UNICODE)


def coverage(intent: Intent, *texts: str) -> int:
    """How many of the query's ideas the texts (a name, a docstring) touch,
    through a word of the same concept or the identifier itself."""
    words: set[str] = set()
    for text in texts:
        if not text:
            continue
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        for w in _SPLIT_WORDS.findall(spaced):
            words |= _keys(w)
    lowered = " ".join(t.lower() for t in texts if t)
    hit = 0
    for key in intent.concepts:
        if key.startswith("#"):
            row = int(key[1:])
            if any(_keys(w) & words for w in SYNONYMS[row]):
                hit += 1
        elif key in words or _stem(key) in words or key in lowered:
            hit += 1
    return hit


def is_test_path(path: str) -> bool:
    """A test file: under a tests/ directory or named test_*.py / *_test.* /
    *.spec.* / *.test.*."""
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    return (
        "/tests/" in p
        or "/test/" in p
        or p.startswith(("tests/", "test/"))
        or name.startswith("test_")
        or "_test." in name
        or ".spec." in name
        or ".test." in name
    )


# A line that defines something, in the languages cgh parses.
DEFINITION_LINE = re.compile(
    r"^\s*(?:"
    r"(?:async\s+)?def\s|class\s|"
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b|"
    r"(?:export\s+)?(?:abstract\s+)?(?:class|interface|enum|type)\s|"
    r"(?:export\s+)?const\s+\w+\s*=\s*(?:async\s*)?(?:\(|function)|"
    r"func\s|(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s|"
    r"(?:pub\s+)?(?:struct|trait|impl)\s|"
    r"(?:public|private|protected|internal|static)\s[^=;]*\(|"
    r"(?:resource|data|module|variable|output)\s+\""
    r")"
)


def is_definition(text: str) -> bool:
    return bool(DEFINITION_LINE.match(text or ""))


# Import in-degree per file, the centrality term the hotspots tool uses,
# cached per repo for a short while: it is one scan of the IMPORTS edges.
_CENTRALITY_TTL = 120.0
_CENTRALITY_SCAN_CAP = 200_000
_CENTRALITY: dict[str, tuple[float, dict[str, int]]] = {}


def importers(conn: Any, root: str | Path | None) -> dict[str, int]:
    """{file path: number of files importing it}, from the IMPORTS edges,
    as hotspots counts it. Empty when the graph cannot answer."""
    key = str(root or "")
    now = time.monotonic()
    cached = _CENTRALITY.get(key)
    if cached and now - cached[0] < _CENTRALITY_TTL:
        return cached[1]
    counts: dict[str, int] = {}
    try:
        for row in conn.find_neighbors(
            "IMPORTS", return_dst=["path"], limit=_CENTRALITY_SCAN_CAP
        ):
            dst = row.get("dst_path")
            if dst:
                counts[dst] = counts.get(dst, 0) + 1
    except Exception:  # a graph without IMPORTS edges ranks on recency alone
        counts = {}
    _CENTRALITY[key] = (now, counts)
    return counts


def clear_centrality_cache() -> None:
    _CENTRALITY.clear()


def _mtime(path: str, cache: dict[str, float]) -> float:
    if path not in cache:
        try:
            cache[path] = os.stat(path).st_mtime
        except OSError:
            cache[path] = 0.0
    return cache[path]


def _bucket(n: int) -> int:
    """Import in-degree in coarse steps, so a file imported 41 times and one
    imported 39 times tie and recency decides between them."""
    return n.bit_length()


def file_order(
    files: Iterable[str],
    defining: set[str],
    central: dict[str, int],
) -> dict[str, int]:
    """Rank of each file: the ones holding a definition first, test files
    after the others, then the most imported, then the most recently
    changed. Returns {file: position}."""
    mtimes: dict[str, float] = {}
    uniq = list(dict.fromkeys(files))
    ranked = sorted(
        uniq,
        key=lambda f: (
            f not in defining,
            is_test_path(f),
            -_bucket(central.get(f, 0)),
            -_mtime(f, mtimes),
        ),
    )
    return {f: i for i, f in enumerate(ranked)}


def rank_hits(hits: list[dict], central: dict[str, int]) -> list[dict]:
    """pattern_search hits ordered by usefulness, file by file (see
    file_order); the hits of one file keep their line order."""
    if not hits:
        return hits
    defining = {h["file"] for h in hits if is_definition(str(h.get("text", "")))}
    order = file_order((h["file"] for h in hits), defining, central)
    return sorted(hits, key=lambda h: (order[h["file"]], h.get("line", 0)))


def rank_definitions(rows: list[dict], central: dict[str, int]) -> list[dict]:
    """Definition rows (symbol_lookup, search_symbols) with code before
    Markdown sections and tests, then the most imported and most recently
    changed files first. Stable for rows that tie."""
    mtimes: dict[str, float] = {}

    def key(row: dict) -> tuple:
        f = str(row.get("file") or "")
        return (
            row.get("kind") == "md_section",
            is_test_path(f),
            -_bucket(central.get(f, 0)),
            -_mtime(f, mtimes) if f else 0.0,
        )

    return sorted(rows, key=key)


def _rel(path: str, roots: list[Path]) -> str:
    if not os.path.isabs(path):
        return path.replace("\\", "/")
    for root in roots:
        try:
            return Path(path).relative_to(root).as_posix()
        except ValueError:
            continue
    return ""


def top_dirs(
    files: Iterable[str], roots: list[Path] | None = None, k: int = 3
) -> list[dict[str, Any]]:
    """The 2 or 3 directories holding most of the hits, as globs a narrower
    pattern_search can take, with their hit counts. ``files`` has one entry
    per hit. Paths are made relative to ``roots`` (absolute paths outside
    every root are skipped); without roots, to their common directory."""
    paths = list(files)
    if roots is None:
        absolute = [p for p in paths if os.path.isabs(p)]
        roots = (
            [Path(os.path.commonpath([os.path.dirname(p) for p in absolute]))]
            if absolute
            else []
        )
    rels = [r for r in (_rel(p, roots) for p in paths) if r]
    if not rels:
        return []
    best: Counter[str] = Counter()
    for depth in (1, 2, 3):
        counts: Counter[str] = Counter()
        for rel in rels:
            parts = rel.split("/")
            if len(parts) == 1:
                ext = os.path.splitext(parts[0])[1]
                counts[f"*{ext}" if ext else parts[0]] += 1
            elif len(parts) - 1 < depth:
                # Directly in a directory a sibling bucket goes deeper into.
                counts["/".join(parts[:-1]) + "/*"] += 1
            else:
                counts["/".join(parts[:depth]) + "/**"] += 1
        best = counts
        top = counts.most_common(1)[0][1]
        if len(counts) >= 2 and top <= 0.7 * len(rels):
            break
    return [{"glob": g, "hits": n} for g, n in best.most_common(k)]


def call(tool: str, *args: Any, **kwargs: Any) -> str:
    """A ready-to-run call, as text: call("symbol_lookup", "x") ->
    'symbol_lookup("x")'."""
    import json

    parts = [json.dumps(a, ensure_ascii=False) for a in args]
    parts += [f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in kwargs.items()]
    return f"{tool}({', '.join(parts)})"


def sentence_confidence(intent: Intent, exact: int, best_cover: int, hits: int) -> str:
    """high when a named identifier resolved to a definition, medium when a
    hit touches two of the query's ideas (or its only one), low otherwise."""
    if exact:
        return "high"
    need = min(2, max(1, len(intent.concepts)))
    if hits and best_cover >= need:
        return "medium"
    return "low"
