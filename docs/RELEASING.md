# Releasing cgh

The runbook for shipping a new version. Work top to bottom; do not skip the
verification at the end. cgh publishes to PyPI via GitHub OIDC (no tokens),
triggered by pushing a `vX.Y.Z` tag whose version matches `pyproject.toml`.

The package name is `cgh`; the import name is `codegraph`. Releases follow
Gitflow: a `release/*` branch off `develop`, merged to `main`, tagged, then
back-merged to `develop`.

## 0. Decide the version (SemVer)

- **PATCH** (`0.5.0 -> 0.5.1`): backwards-compatible bug fixes only.
- **MINOR** (`0.5.0 -> 0.6.0`): backwards-compatible new functionality (new
  MCP tools, CLI commands, parsers, extras).
- **MAJOR**: breaking changes. Still `0.x`, so the API is not yet contractually
  stable, but avoid surprises.

PyPI does NOT allow re-uploading a version. A failed or wrong release means
bumping to the next number, never re-tagging the same one.

## 1. Pre-flight (on `develop`)

```bash
git checkout develop && git pull --ff-only
uv run pytest -q                      # full suite green
uv run pytest --extra kuzu -q         # green with the optional kuzu backend too
uv run ruff check . && uv run ruff format --check .
python3 scripts/check_no_em_dashes.py # prose guard (no em/en dashes)
uv lock --check                       # lockfile in sync with pyproject
```

- [ ] All of the above pass on a clean `develop`.
- [ ] Optional extras still install and their guarded tests run, not just skip:
      `uv pip install -e ".[langs,lsp,kuzu]"` then `uv run pytest -q`.

## 2. Version (single source of truth)

The version lives in **one** place: `pyproject.toml`. `codegraph/__init__.py`
reads `__version__` from installed package metadata, so do NOT hardcode a
version there or anywhere else (this is what made 0.5.0 ship a `0.4.6` banner).

- [ ] Bump `version = "X.Y.Z"` in `pyproject.toml`.
- [ ] Grep for stray hardcoded versions: `grep -rn "X\.Y\.(Z-1)" codegraph/`
      should return nothing in source (CHANGELOG mentions are fine).
- [ ] `uv lock` to refresh `uv.lock` (its `cgh` entry must match the new
      version). CI runs `uv lock --check`.

## 3. CHANGELOG

`CHANGELOG.md` follows Keep a Changelog.

- [ ] Move the `## [Unreleased]` content into a new `## [X.Y.Z] - YYYY-MM-DD`
      section (use today's real date), grouped under Added / Changed / Fixed /
      Security. Leave an empty `## [Unreleased]` at the top.
- [ ] Update the link references at the bottom: add the new compare link and
      point `[Unreleased]` at `vX.Y.Z...HEAD`.
- [ ] No em-dashes (the prose guard scans CHANGELOG).

## 4. README and docs

Bring user-facing docs in line with what shipped.

- [ ] **README MCP tool count** matches reality:
      `grep -rc '@mcp.tool()' codegraph/server/tools_*.py | awk -F: '{s+=$2} END {print s}'`
- [ ] New MCP tools listed in the MCP Tools section.
- [ ] New CLI commands / flags documented in the CLI Reference, and added to the
      `_print_help()` landing screen in `codegraph/__main__.py`.
- [ ] New parsers in the Supported Languages table; new install extras in the
      Install section (quoted, e.g. `pip install "cgh[langs,lsp]"`).
- [ ] New config options / env vars in the Configuration section.
- [ ] Limitations and Security sections still accurate (do not describe removed
      behavior).
- [ ] `docs/CLI_REFERENCE.md`, `docs/CONFIGURATION.md`, `docs/PARSERS.md`
      updated if the relevant surface changed.

## 5. Headers and code hygiene

- [ ] Every new source file starts with the ALTIKVA header block and a
      `# Description:` line (see the root `CLAUDE.md`). Author/maintainer stay
      the human, never an AI; no AI attribution anywhere (commits, PRs, files).
- [ ] New `.py` files use type hints at boundaries and the `cgh` CLI banner
      convention if they add a command.
- [ ] No build artifacts staged (`dist/`, `build/`, `*.egg-info/`).

## 6. Cut the release (Gitflow)

```bash
git checkout -b release/X.Y.Z develop
# steps 2 to 5 land on this branch
git commit -am "release: vX.Y.Z"
git push -u origin release/X.Y.Z
gh pr create --base main --head release/X.Y.Z --title "release: vX.Y.Z"
# wait for CI green, then:
gh pr merge --merge release/X.Y.Z
```

- [ ] Release PR targets `main`, CI green, merged.

## 7. Tag and publish

```bash
git checkout main && git pull --ff-only
git tag vX.Y.Z          # tag must equal pyproject version
git push origin vX.Y.Z  # triggers .github/workflows/release.yml
```

- [ ] The `Release` workflow run reaches the `pypi` environment **approval
      gate** and waits. A maintainer approves it in the GitHub UI
      (Actions -> the run -> Review deployments -> approve `pypi`). Nothing
      publishes until approved.

## 8. Back-merge to `develop`

`main` now has the release commit (version + CHANGELOG) that `develop` lacks.

```bash
git checkout -b chore/back-merge-X.Y.Z main
git push -u origin chore/back-merge-X.Y.Z
gh pr create --base develop --head chore/back-merge-X.Y.Z \
  --title "merge: main into develop (release vX.Y.Z back-merge)"
gh pr merge --merge --delete-branch chore/back-merge-X.Y.Z
```

- [ ] `develop` and `main` both read `X.Y.Z`.

## 9. Post-release verification

- [ ] PyPI shows the new version with both artifacts:
      `curl -s https://pypi.org/pypi/cgh/json | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['info']['version'], list(d['releases'].get('X.Y.Z',[]) and [f['filename'] for f in d['releases']['X.Y.Z']]))"`
- [ ] GitHub Release `vX.Y.Z` exists with the wheel + sdist attached:
      `gh release view vX.Y.Z`
- [ ] A clean install resolves and reports the right version, banner included:
      `uvx cgh@X.Y.Z --version` (should print `codegraph X.Y.Z`, not a stale
      number).
- [ ] Reinstall the local tool from the released version if you use it:
      `uv tool install --force "cgh==X.Y.Z"`.

## Gotchas learned the hard way

- **One version, one place.** Only `pyproject.toml`. `__version__` derives from
  metadata. If you ever see the banner disagree with PyPI, something
  re-hardcoded it.
- **`uv` caches wheels by version.** Re-running `uv tool install .` after an
  edit that did NOT change the version reuses the cached wheel. Use
  `uv cache clean cgh` or bump the version to force a rebuild. Also note
  `uv tool install .` builds from committed `HEAD`, not the dirty working tree.
- **PyPI is append-only.** No re-uploads. Wrong release -> next patch number.
- **The release workflow only checks the tag against `pyproject.toml`.** It
  does not lint the README or the CHANGELOG, so this checklist is the guard for
  those.
- **Dual license stays conjunctive** (MIT AND CC BY-NC-SA). Keep both
  classifiers in `pyproject.toml` and the per-file headers as `MIT & CC BY-NC-SA`.
- **Do not add a `codegraph` CLI alias** and do not rename the `codegraph`
  import path. `cgh` is the only entry point; the name asymmetry is intentional.
