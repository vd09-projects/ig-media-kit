"""T17 — STATIC import-boundary guard for the zero-IG invariant.

The CQRS hard split makes ``list_reels`` a pure READ-ONLY query that must NEVER
touch Instagram on any code path. Two runtime poison tests (test_list_reels.py)
already prove zero network on the served + not-analyzed branches, but each only
exercises ONE branch — a future refactor that imported ``fetch_window`` and called
it on some THIRD branch would slip past both.

This test closes that gap statically: it AST-parses ``list_reels`` (without
executing the module) and asserts it imports NEITHER the metered fetch module
(``ig_media_kit.fetch``) NOR the HTTP transport (``ig_media_kit.http_client`` /
``AnonymousClient``). It converts the build-time grep acceptance into a permanent
CI regression tripwire for the invariant that defines this whole change.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

PACKAGE = "ig_media_kit"
FORBIDDEN_MODULES = {"ig_media_kit.fetch", "ig_media_kit.http_client"}
FORBIDDEN_NAMES = {"AnonymousClient", "fetch_window", "resolve_user_id"}


def _module_source(dotted: str) -> tuple[str, str]:
    """Locate a module's source WITHOUT executing it (find_spec imports only the
    parent package, never the target module) — a purely static read."""
    spec = importlib.util.find_spec(dotted)
    assert spec is not None and spec.origin, f"cannot locate source for {dotted}"
    return Path(spec.origin).read_text(), spec.origin


def _resolve_relative(module: str | None, level: int) -> str | None:
    """Resolve a (possibly relative) ImportFrom target to a dotted path. The
    targets under test live directly in the ``ig_media_kit`` package, so any
    level>0 relative import resolves against the package root."""
    if level == 0:
        return module
    return f"{PACKAGE}.{module}" if module else PACKAGE


def _collect_imports(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Walk the ENTIRE module (top-level AND nested/lazy imports inside any
    function) and return (imported module paths, imported symbol names), with
    relative and ``from ig_media_kit import X`` forms normalized to dotted paths."""
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_relative(node.module, node.level)
            if resolved:
                modules.add(resolved)
            for alias in node.names:
                names.add(alias.name)
                # ``from . import fetch`` — the imported NAME is a submodule.
                if node.module is None and node.level > 0:
                    modules.add(_resolve_relative(alias.name, node.level))
                # ``from ig_media_kit import fetch`` — absolute submodule import.
                elif node.level == 0 and node.module == PACKAGE:
                    modules.add(f"{PACKAGE}.{alias.name}")
    return modules, names


def test_list_reels_imports_neither_fetch_nor_http_client():
    # Positive control first: prove the AST detector actually FIRES on a known
    # violator (``fill.py`` genuinely imports both forbidden modules + the client).
    # A detector that trivially passed would give false assurance otherwise.
    fill_src, fill_origin = _module_source("ig_media_kit.fill")
    fill_modules, fill_names = _collect_imports(ast.parse(fill_src, filename=fill_origin))
    assert "ig_media_kit.fetch" in fill_modules
    assert "ig_media_kit.http_client" in fill_modules
    assert "AnonymousClient" in fill_names

    # The actual boundary assertion: list_reels imports NONE of them.
    src, origin = _module_source("ig_media_kit.list_reels")
    modules, names = _collect_imports(ast.parse(src, filename=origin))

    offending_modules = modules & FORBIDDEN_MODULES
    offending_names = names & FORBIDDEN_NAMES
    assert not offending_modules, (
        "list_reels must be zero-IG: it imports metered/transport module(s) "
        f"{sorted(offending_modules)} — the fetch path belongs in fill.py"
    )
    assert not offending_names, (
        "list_reels must be zero-IG: it imports network primitive(s) "
        f"{sorted(offending_names)} — the fetch path belongs in fill.py"
    )
