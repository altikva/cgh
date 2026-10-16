# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The `cgh proxy` verbs.
#              serve  run the proxy on 127.0.0.1 in front of the gateway
#              list   the last exchanges from the journal: size, tokens, PII
#              show   one exchange: anatomy of the request, usage, PII
#                     counts; --raw prints the captured bodies
#              Settings come from flags and CGH_PROXY_* variables only,
#              never from a repo's .codegraph/config.toml: a cloned repo
#              could otherwise point the proxy, and your gateway key, at
#              its own server.

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from . import ProxyError

if TYPE_CHECKING:
    from .capture import CaptureStore

_EXAMPLE = "https://litellm.example.com"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"CGH_PROXY_{name}", default)


def make_cli_registrar():
    def register_cli(subparsers) -> None:
        p = subparsers.add_parser(
            "proxy",
            help="Experimental: local proxy between Claude Code and a LiteLLM "
            "gateway (mTLS, last exchanges kept, PII observed)",
        )
        p.add_argument("action", choices=["serve", "list", "show"])
        p.add_argument("id", nargs="?", default="", help="show: the capture id")
        p.add_argument(
            "--upstream",
            default=_env("UPSTREAM"),
            help=f"serve: the LiteLLM gateway URL, e.g. {_EXAMPLE}",
        )
        p.add_argument("--port", type=int, default=int(_env("PORT", "8787")))
        p.add_argument(
            "--keep",
            type=int,
            default=int(_env("KEEP", "20")),
            help="serve: full captures kept, raw bodies included (0: none, "
            "the journal still records sizes, tokens and PII counts)",
        )
        p.add_argument(
            "--dir",
            default=_env("DIR"),
            help="Where captures live (default ~/.cache/cgh/proxy)",
        )
        p.add_argument("--cert", default=_env("CERT"),
                       help="serve: client certificate for mTLS (PEM)")  # fmt: skip
        p.add_argument("--key", default=_env("KEY"),
                       help="serve: its private key (PEM)")  # fmt: skip
        p.add_argument("--ca", default=_env("CA"),
                       help="serve: CA that signed the gateway certificate")  # fmt: skip
        p.add_argument("--no-scan", action="store_true",
                       help="serve: do not scan outbound requests for PII")  # fmt: skip
        p.add_argument("-n", type=int, default=20, help="list: how many")
        p.add_argument("--json", action="store_true", help="list / show: JSON")
        p.add_argument("--raw", action="store_true", help="show: the full capture")
        p.set_defaults(func=lambda args: _dispatch(args))

    return register_cli


def _path(value: str) -> Path | None:
    return Path(value).expanduser() if value else None


def _dispatch(args) -> None:
    from rich.console import Console

    from .capture import CaptureStore, default_dir

    err = Console(stderr=True)
    root = _path(args.dir) or default_dir()
    try:
        if args.action == "serve":
            _serve(err, args, root)
        elif args.action == "list":
            _list(args, CaptureStore(root, keep=0))
        else:
            _show(args, CaptureStore(root, keep=0))
    except ProxyError as exc:
        err.print(f"[red]{exc}[/red]")
        raise SystemExit(2) from exc


def _serve(err, args, root: Path) -> None:
    from .app import ProxyConfig, check_upstream, serve, tls_context

    if not args.upstream:
        raise ProxyError(f"--upstream is required, e.g. --upstream {_EXAMPLE}")
    cfg = ProxyConfig(
        upstream=check_upstream(args.upstream),
        capture_dir=root,
        keep=args.keep,
        scan=not args.no_scan,
        client_cert=_path(args.cert),
        client_key=_path(args.key),
        ca_cert=_path(args.ca),
    )
    tls_context(cfg)  # fail now on a bad certificate, not on the first call
    mtls = (
        "with a client certificate" if cfg.client_cert else "without client certificate"
    )
    err.print(f"cgh proxy on http://127.0.0.1:{args.port} -> {cfg.upstream} ({mtls})")
    err.print(f"captures: {root} (last {cfg.keep})")
    err.print(
        f"In the Claude Code shell: export ANTHROPIC_BASE_URL=http://127.0.0.1:{args.port}"
    )
    serve(cfg, args.port)


def _kb(n: int | None) -> str:
    return f"{(n or 0) / 1024:.1f}"


def _pii_label(pii: dict | None) -> str:
    if not pii:
        return "-"
    if "skipped" in pii:
        return "not scanned"
    counts = pii.get("last_message") or {}
    return (
        ", ".join(f"{k.split('.', 1)[-1]} {v}" for k, v in sorted(counts.items()))
        or "0"
    )


def _list(args, store: CaptureStore) -> None:
    rows = store.recent(args.n)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    from rich.console import Console
    from rich.table import Column, Table

    # The id is what `cgh proxy show` takes: never let the table cut it.
    table = Table(Column("id", no_wrap=True, min_width=18), "status", "s", "model", "req KB",
                  "msgs", "in tok", "cache read", "out tok", "PII (new)")  # fmt: skip
    for row in rows:
        req = row.get("request") or {}
        usage = (row.get("response") or {}).get("usage") or {}
        table.add_row(
            row.get("id", ""),
            str(row.get("status", "")),
            f"{row.get('ms', 0) / 1000:.1f}",
            str(req.get("model") or row.get("path", "")),
            _kb(row.get("request_bytes")),
            str(req.get("messages", "")),
            str(usage.get("input_tokens", "")),
            str(usage.get("cache_read_input_tokens", "")),
            str(usage.get("output_tokens", "")),
            _pii_label(row.get("pii")),
        )
    Console().print(table)


def _show(args, store: CaptureStore) -> None:
    if not args.id:
        raise ProxyError("show needs a capture id (see cgh proxy list)")
    cap = store.load(args.id)
    if args.raw or args.json:
        print(
            json.dumps(
                cap if args.raw else cap["summary"], ensure_ascii=False, indent=2
            )
        )
        return
    summary = cap["summary"]
    req = summary.get("request") or {}
    resp = summary.get("response") or {}
    print(f"{summary['method']} {summary['path']} -> {summary['status']} "
          f"in {summary['ms'] / 1000:.1f} s")  # fmt: skip
    if req:
        print(f"model {req.get('model')}, stream {req.get('stream')}, "
              f"{req.get('messages')} messages, last from {req.get('last_role')}")  # fmt: skip
        print(f"  system   {_kb(req.get('system_bytes')):>8} KB")
        print(
            f"  tools    {_kb(req.get('tools_bytes')):>8} KB  ({req.get('tools')} tools)"
        )
        print(f"  messages {_kb(req.get('messages_bytes')):>8} KB")
        for kind, size in (req.get("block_bytes") or {}).items():
            print(f"    {kind:<12} {_kb(size):>8} KB")
        print(f"  last msg {_kb(req.get('last_bytes')):>8} KB")
    if resp:
        print(f"usage {resp.get('usage')}, stop {resp.get('stop_reason')}")
    pii = summary.get("pii")
    if pii:
        print(f"PII whole request {pii.get('request', pii)}")
        print(f"PII last message  {pii.get('last_message', '-')}")
