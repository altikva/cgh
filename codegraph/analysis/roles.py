# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Classify files by architectural role based on path conventions.
#              Works across Python/FastAPI, Nuxt/Vue, Terraform, and Markdown.
#              Used by `architecture_overview`, `domain_map` MCP tools.

from __future__ import annotations

import re
from pathlib import Path

# Framework-agnostic defaults. Each rule is (path fragment, role, layer).
# Matched as a substring against the POSIX-normalised relative path.
# Teams with different conventions can override via .codegraph/config.toml
# (see load_custom_rules).
#
# Coverage:
#   - Python (FastAPI, Flask, Django)
#   - JavaScript / TypeScript (Nuxt, Next.js, Remix, Express)
#   - Vue, Svelte, React, Solid
#   - Terraform, Kubernetes
#   - Docs, tests, scripts
_DEFAULT_PATH_RULES: list[tuple[str, str, str]] = [
    # --- Backend, routing / HTTP ---
    ("/routers/", "router", "presentation"),
    ("/routes/", "router", "presentation"),
    ("/controllers/", "controller", "presentation"),
    ("/webhooks/", "webhook", "presentation"),
    ("/middleware/", "middleware", "presentation"),
    ("/server/api/", "api_route", "presentation"),
    ("/server/middleware/", "middleware", "presentation"),
    # --- Backend, business logic ---
    ("/handlers/", "handler", "application"),
    ("/managers/", "manager", "application"),
    ("/services/", "service", "application"),
    ("/usecases/", "usecase", "application"),
    ("/use_cases/", "usecase", "application"),
    ("/interactors/", "interactor", "application"),
    ("/actions/", "action", "application"),
    ("/tasks/", "task", "application"),
    ("/jobs/", "job", "application"),
    ("/workers/", "worker", "application"),
    ("/events/", "event", "application"),
    ("/subscribers/", "subscriber", "application"),
    ("/validators/", "validator", "application"),
    # --- Backend, domain ---
    ("/models/", "model", "domain"),
    ("/entities/", "entity", "domain"),
    ("/domain/", "domain_obj", "domain"),
    ("/schemas/", "schema", "domain"),
    ("/dto/", "dto", "domain"),
    ("/types/", "type", "domain"),
    # --- Backend, infra ---
    ("/providers/", "provider", "infra"),
    ("/adapters/", "adapter", "infra"),
    ("/repositories/", "repository", "infra"),
    ("/repos/", "repository", "infra"),
    ("/clients/", "client", "infra"),
    ("/integrations/", "integration", "infra"),
    ("/utils/", "util", "infra"),
    ("/lib/", "lib", "infra"),
    ("/helpers/", "util", "infra"),
    ("/templates/", "template", "presentation"),
    # --- Migrations / DB ---
    ("/migrations/", "migration", "infra"),
    ("/alembic/versions/", "migration", "infra"),
    ("/prisma/migrations/", "migration", "infra"),
    # --- Frontend, presentation ---
    ("/components/", "component", "presentation"),
    ("/pages/", "page", "presentation"),
    ("/views/", "view", "presentation"),
    ("/layouts/", "layout", "presentation"),
    ("/routes/", "page", "presentation"),  # Remix / SvelteKit
    ("/app/", "page", "presentation"),  # Next.js app router
    # --- Frontend, logic ---
    ("/composables/", "composable", "application"),
    ("/hooks/", "hook", "application"),
    ("/stores/", "store", "application"),
    ("/store/", "store", "application"),
    ("/contexts/", "context", "application"),
    # --- Monorepo ---
    ("/apps/", "app", "presentation"),
    ("/packages/", "package", "infra"),
    ("/workspaces/", "package", "infra"),
    # --- Infra ---
    ("/modules/", "tf_module", "infra"),
    ("/environments/", "tf_env", "infra"),
    ("/envs/", "tf_env", "infra"),
    ("/charts/", "helm_chart", "infra"),
    ("/manifests/", "k8s_manifest", "infra"),
    ("/k8s/", "k8s_manifest", "infra"),
    # --- Scripts / tooling ---
    ("/scripts/", "script", "infra"),
    ("/bin/", "script", "infra"),
    ("/tools/", "script", "infra"),
    # --- Docs ---
    ("/docs/", "doc", "doc"),
    ("/documentation/", "doc", "doc"),
    # --- Tests ---
    ("/tests/", "test", "test"),
    ("/test/", "test", "test"),
    ("/__tests__/", "test", "test"),
    ("/spec/", "test", "test"),
    ("/e2e/", "test", "test"),
    ("/cypress/", "test", "test"),
]

# Fallback: filename-based classification
_NAME_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(^|_)handler\.py$", re.I), "handler", "application"),
    (re.compile(r"(^|_)manager\.py$", re.I), "manager", "application"),
    (re.compile(r"(^|_)service\.py$", re.I), "service", "application"),
    (re.compile(r"(^|_)router\.py$", re.I), "router", "presentation"),
    (re.compile(r"(^|_)adapter\.py$", re.I), "provider", "infra"),
    (re.compile(r"(^|_)parser\.py$", re.I), "parser", "infra"),
    (re.compile(r"(^|_)subscriber\.py$", re.I), "subscriber", "application"),
    (re.compile(r"^conftest\.py$", re.I), "test", "test"),
    (re.compile(r"^test_.*\.py$", re.I), "test", "test"),
    (re.compile(r"\.test\.(ts|js|tsx|jsx)$", re.I), "test", "test"),
    (re.compile(r"\.spec\.(ts|js|tsx|jsx)$", re.I), "test", "test"),
    (re.compile(r"\.vue$", re.I), "component", "presentation"),
    (re.compile(r"\.tf$", re.I), "tf_resource", "infra"),
    (re.compile(r"\.md$", re.I), "doc", "doc"),
    (re.compile(r"\.mdx$", re.I), "doc", "doc"),
    (re.compile(r"pyproject\.toml$", re.I), "manifest", "infra"),
    (re.compile(r"package\.json$", re.I), "manifest", "infra"),
    (re.compile(r"Dockerfile$", re.I), "container", "infra"),
    (re.compile(r"docker-compose\.ya?ml$", re.I), "container", "infra"),
]


_CUSTOM_RULES_CACHE: dict[str, list[tuple[str, str, str]]] = {}


def _load_custom_rules(repo_root: str | Path) -> list[tuple[str, str, str]]:
    """
    Load team-specific role rules from .codegraph/config.toml if present.
    Teams with non-standard layouts (e.g. src/domain/handler rather than
    api/handlers) can override without forking codegraph.

    Config syntax:

      [roles]
      # Each rule is <path_fragment> = "<role>:<layer>"
      # Matched as case-insensitive substrings against POSIX rel paths.
      # Custom rules are tried BEFORE built-in defaults.
      "/src/handlers/"      = "handler:application"
      "/src/domain/"        = "model:domain"
      "/app/server/routes/" = "router:presentation"

    Returns the list of custom rules; empty when config missing.
    """
    key = str(Path(repo_root).resolve())
    if key in _CUSTOM_RULES_CACHE:
        return _CUSTOM_RULES_CACHE[key]

    rules: list[tuple[str, str, str]] = []
    cfg = Path(repo_root) / ".codegraph" / "config.toml"
    if cfg.exists():
        try:
            import tomllib

            with open(cfg, "rb") as f:
                data = tomllib.load(f)
            for fragment, value in (data.get("roles") or {}).items():
                if not isinstance(value, str) or ":" not in value:
                    continue
                role, layer = value.split(":", 1)
                rules.append((fragment.lower(), role.strip(), layer.strip()))
        except Exception:
            pass

    _CUSTOM_RULES_CACHE[key] = rules
    return rules


def reset_rules_cache() -> None:
    """Clear the per-repo custom rules cache. Call after config.toml edits."""
    _CUSTOM_RULES_CACHE.clear()


def classify(path: str | Path, repo_root: str | Path | None = None) -> tuple[str, str]:
    """
    Return (role, layer) for a file path.

    role:  narrow semantic category, "handler", "component", "router", etc.
    layer: coarse architectural layer, "presentation" | "application" |
           "domain" | "infra" | "test" | "doc" | "other".

    Matching order:
      1. Custom rules from .codegraph/config.toml [roles]
      2. Built-in path-fragment defaults (longest-match wins)
      3. Filename patterns
      4. ("other", "other")
    """
    p = Path(path)
    try:
        rel = p.relative_to(repo_root) if repo_root else p
    except ValueError:
        rel = p

    rel_posix = "/" + str(rel).replace("\\", "/").lstrip("/")  # ensure leading slash
    rel_lower = rel_posix.lower()

    # 1. Custom rules first, team overrides the built-ins
    if repo_root is not None:
        for fragment, role, layer in _load_custom_rules(repo_root):
            if fragment in rel_lower:
                return role, layer

    # 2. Built-in defaults, prefer the MOST SPECIFIC match (longest wins)
    best_match: tuple[str, str] | None = None
    best_len = -1
    for fragment, role, layer in _DEFAULT_PATH_RULES:
        if fragment in rel_lower and len(fragment) > best_len:
            best_match = (role, layer)
            best_len = len(fragment)

    if best_match is not None:
        return best_match

    # 3. Fallback: name pattern
    for pat, role, layer in _NAME_RULES:
        if pat.search(p.name):
            return role, layer

    return "other", "other"


# Public: ordered layer list for grouping in UI outputs
LAYER_ORDER = ("presentation", "application", "domain", "infra", "test", "doc", "other")
