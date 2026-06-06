"""SECURITY/PURITY GATE: ``app/tax/**`` and ``tax_engine.py`` are LLM-free.

The deterministic tax-computation layer must never depend on a network
client, a language model, or an exception-capture path that calls home.
If an engineer accidentally imports ``services.llm`` or ``httpx`` into a
tax module, this test fails LOUDLY at CI time and locally.

Why a pytest test instead of a workflow grep step?
  * It runs on every developer's machine before push.
  * It uses Python's own AST so renamed imports (``from app.services import
    llm as foo``) are caught.
  * The failure message names the offending file + line.

The rule is: every module under ``app/tax`` and the file
``app/agents/tax_engine.py`` must not import — directly or via ``from`` —
any of the banned modules below.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

_BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_APP_ROOT: Final[Path] = _BACKEND_ROOT / "app"

# A banned module name matches if it equals one of these OR starts with
# one of them followed by a dot. Top-level network/LLM clients only —
# stdlib and pydantic are fine.
_BANNED_TOP_LEVEL: Final[frozenset[str]] = frozenset(
    {"httpx", "openai", "anthropic", "requests", "sentry_sdk", "aiohttp"}
)

# Banned project-internal modules. ``services.llm`` is the unified LLM
# client; importing it from tax/** is the exact mistake this gate prevents.
_BANNED_INTERNAL: Final[frozenset[str]] = frozenset(
    {
        "app.services.llm",
        "app.services.whisper",
        "app.services.embedding",
    }
)


def _scan_targets() -> list[Path]:
    """Every file the no-LLM gate covers."""
    targets: list[Path] = sorted((_APP_ROOT / "tax").rglob("*.py"))
    engine = _APP_ROOT / "agents" / "tax_engine.py"
    if engine.exists():
        targets.append(engine)
    return [p for p in targets if p.name != "__init__.py" or p.stat().st_size > 0]


def _is_banned(name: str) -> bool:
    if name in _BANNED_TOP_LEVEL:
        return True
    if any(name.startswith(prefix + ".") for prefix in _BANNED_TOP_LEVEL):
        return True
    if name in _BANNED_INTERNAL:
        return True
    return any(name.startswith(prefix + ".") for prefix in _BANNED_INTERNAL)


@pytest.mark.parametrize("path", _scan_targets(), ids=lambda p: str(p.relative_to(_APP_ROOT)))
def test_no_llm_imports(path: Path) -> None:
    """Each tax-layer file must not import an LLM / HTTP / Sentry module."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_banned(alias.name):
                    offenders.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_banned(module):
                offenders.append((node.lineno, module))
    if offenders:
        rel = path.relative_to(_BACKEND_ROOT)
        msg = ", ".join(f"{name}@line{ln}" for ln, name in offenders)
        pytest.fail(f"{rel} imports banned LLM/HTTP module(s): {msg}")
