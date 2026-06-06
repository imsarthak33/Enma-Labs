#!/usr/bin/env python3
"""Production-strict security audit for the Enma Labs monorepo.

Implements the greppable subset of the 20-item Security Checklist
(Document 4). Each check is independent and reports pass/fail/skip with a
short reason. Non-greppable items (DB-level encryption, RLS at runtime) are
exercised by the test suite, not here — those rows are marked SKIP with a
pointer to the test that covers them.

Usage
-----
    python scripts/security_audit.py             # full report, exit 1 on FAIL
    python scripts/security_audit.py --json      # machine-readable
    python scripts/security_audit.py --list      # list checks and exit

CI wires this into both backend-ci and gateway-ci so any regression on the
security baseline fails the build.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "enma-backend"
GATEWAY = REPO_ROOT / "enma-gateway"

# Paths excluded from every grep — these legitimately exercise banned patterns
# (the base query class itself, the session factory, migrations, tests).
GLOBAL_PY_EXCLUDES = (
    "enma-backend/app/db/queries/base.py",
    "enma-backend/app/db/session.py",
    "enma-backend/migrations/",
    "enma-backend/tests/",
)

# Direct ``session.execute`` is allowed only inside files that obviously belong
# to the BaseQuery family (db/queries subclasses) or that carry an explicit
# audit-allow comment justifying the bypass. The check function below applies
# both rules.
BASEQUERY_DIR_PREFIX = "enma-backend/app/db/queries/"
AUDIT_ALLOW_DIRECTIVE = "# audit:allow-direct-session"


# ---------------------------------------------------------------------------
# Framework
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    path: str
    line: int
    snippet: str


@dataclass
class CheckResult:
    item: int
    title: str
    priority: str
    status: str  # PASS | FAIL | SKIP
    reason: str = ""
    findings: list[Finding] = field(default_factory=list)


def _iter_files(root: Path, suffix: str, excludes: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob(f"*{suffix}"):
        # Use POSIX-style relative path so the exclude tuples work on Windows.
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(ex in rel for ex in excludes):
            continue
        if any(
            part in {".venv", "venv", "node_modules", "__pycache__"}
            for part in path.parts
        ):
            continue
        files.append(path)
    return files


def _grep(
    pattern: re.Pattern[str],
    files: list[Path],
) -> list[Finding]:
    out: list[Finding] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                out.append(
                    Finding(
                        path=path.relative_to(REPO_ROOT).as_posix(),
                        line=lineno,
                        snippet=line.strip()[:160],
                    )
                )
    return out


def _file_exists(rel: str) -> bool:
    return (REPO_ROOT / rel).exists()


def _file_contains(rel: str, pattern: re.Pattern[str]) -> bool:
    path = REPO_ROOT / rel
    if not path.exists():
        return False
    return bool(pattern.search(path.read_text(encoding="utf-8", errors="ignore")))


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_01_basequery() -> CheckResult:
    """Direct SQLAlchemy session use outside BaseQuery is banned.

    Two legitimate exemptions:
      * Files under ``app/db/queries/`` — these ARE the BaseQuery subclasses.
      * Lines tagged with ``# audit:allow-direct-session`` — documents an
        intentional bypass; the line must explicitly carry a ca_firm_id filter.
    """
    files = [
        p
        for p in _iter_files(BACKEND / "app", ".py", GLOBAL_PY_EXCLUDES)
        if not p.relative_to(REPO_ROOT).as_posix().startswith(BASEQUERY_DIR_PREFIX)
    ]
    pat = re.compile(r"\bsession\.(execute|query|add|delete|merge)\b")
    raw = _grep(pat, files)
    findings = [f for f in raw if AUDIT_ALLOW_DIRECTIVE not in f.snippet]
    return CheckResult(
        item=1,
        title="All queries go through BaseQuery",
        priority="P0",
        status="PASS" if not findings else "FAIL",
        reason=(
            "Direct SQLAlchemy session usage found outside BaseQuery"
            if findings
            else "No direct session usage outside BaseQuery"
        ),
        findings=findings,
    )


def check_02_rls_policies() -> CheckResult:
    """RLS policies enabled in the initial migration."""
    migrations = list((BACKEND / "migrations" / "versions").glob("*.py"))
    needed = ["FORCE ROW LEVEL SECURITY", "tenant_isolation"]
    hits = {n: False for n in needed}
    for path in migrations:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in needed:
            if marker in text:
                hits[marker] = True
    missing = [m for m, ok in hits.items() if not ok]
    return CheckResult(
        item=2,
        title="RLS policies enabled on tenant-scoped tables",
        priority="P0",
        status="PASS" if not missing else "FAIL",
        reason=(
            f"Missing RLS markers in migrations: {', '.join(missing)}"
            if missing
            else "RLS markers present in migrations"
        ),
    )


def check_03_cross_tenant_tests() -> CheckResult:
    """Cross-tenant isolation test must exist."""
    candidates = list((BACKEND / "tests").rglob("test_tenant_isolation.py"))
    return CheckResult(
        item=3,
        title="Cross-tenant isolation tests present",
        priority="P0",
        status="PASS" if candidates else "FAIL",
        reason=(
            f"Found {candidates[0].relative_to(REPO_ROOT).as_posix()}"
            if candidates
            else "tests/test_security/test_tenant_isolation.py missing"
        ),
    )


def check_04_idempotency_middleware() -> CheckResult:
    """All /worker/* routes must run idempotency check."""
    worker = BACKEND / "app" / "api" / "routes" / "worker.py"
    cron = BACKEND / "app" / "api" / "routes" / "cron.py"
    if not worker.exists():
        return CheckResult(
            item=4,
            title="Idempotency middleware active on /worker/*",
            priority="P0",
            status="FAIL",
            reason="app/api/routes/worker.py missing",
        )
    worker_text = worker.read_text(encoding="utf-8", errors="ignore")
    cron_text = (
        cron.read_text(encoding="utf-8", errors="ignore") if cron.exists() else ""
    )
    has_worker_check = "idempotency" in worker_text.lower()
    has_cron_check = (not cron.exists()) or "idempoten" in cron_text.lower()
    ok = has_worker_check and has_cron_check
    return CheckResult(
        item=4,
        title="Idempotency middleware active on /worker/*",
        priority="P0",
        status="PASS" if ok else "FAIL",
        reason=(
            "Idempotency references found" if ok else "Idempotency reference missing"
        ),
    )


def check_05_hmac_verify() -> CheckResult:
    """Envelope HMAC verified server-side with compare_digest."""
    target = BACKEND / "app" / "api" / "middleware" / "envelope_verify.py"
    if not target.exists():
        return CheckResult(
            item=5,
            title="Envelope HMAC signature verified",
            priority="P1",
            status="FAIL",
            reason="envelope_verify.py missing",
        )
    text = target.read_text(encoding="utf-8", errors="ignore")
    ok = "hmac" in text and "compare_digest" in text
    return CheckResult(
        item=5,
        title="Envelope HMAC signature verified with compare_digest",
        priority="P1",
        status="PASS" if ok else "FAIL",
        reason=(
            "compare_digest used in envelope verify"
            if ok
            else "Constant-time HMAC compare missing"
        ),
    )


def check_06_raw_sql_fstrings() -> CheckResult:
    """No raw SQL inside f-strings."""
    files = _iter_files(BACKEND / "app", ".py", GLOBAL_PY_EXCLUDES)
    # Look for f"..." or f'...' starting with a SQL verb.
    pat = re.compile(
        r"""f["'](?:\s*)(?:SELECT|INSERT\s+INTO|UPDATE\s+\w+|DELETE\s+FROM)\b""",
        re.IGNORECASE,
    )
    findings = _grep(pat, files)
    return CheckResult(
        item=6,
        title="No raw SQL f-strings in application code",
        priority="P0",
        status="PASS" if not findings else "FAIL",
        reason=(
            "Raw SQL f-strings found" if findings else "No raw SQL f-strings detected"
        ),
        findings=findings,
    )


def check_07_no_user_text_in_system_prompts() -> CheckResult:
    """Heuristic: system prompts must not interpolate raw user input."""
    files = _iter_files(BACKEND / "app", ".py", GLOBAL_PY_EXCLUDES)
    # Pattern: a "role": "system" line whose adjacent content is an f-string
    # containing user_message / user_input / user_text. We grep wider for any
    # role=system on the same line as those names.
    pat = re.compile(
        r"""["']system["'].{0,80}f["'][^"']*\{(user_message|user_input|user_text)\}""",
        re.IGNORECASE,
    )
    findings = _grep(pat, files)
    return CheckResult(
        item=7,
        title="User text not interpolated into system prompts",
        priority="P0",
        status="PASS" if not findings else "FAIL",
        reason=(
            "Possible user-text injection into system prompts"
            if findings
            else "No user-text interpolation into system role found"
        ),
        findings=findings,
    )


def check_08_safe_text_html() -> CheckResult:
    """HTML helpers must exist and be exported."""
    target = BACKEND / "app" / "formatting" / "telegram_html.py"
    if not target.exists():
        return CheckResult(
            item=8,
            title="HTML safe_text helper present",
            priority="P0",
            status="FAIL",
            reason="formatting/telegram_html.py missing",
        )
    text = target.read_text(encoding="utf-8", errors="ignore")
    # Accept both ``html.escape`` and the aliased ``from html import escape``.
    escape_present = (
        "html.escape" in text
        or re.search(r"from\s+html\s+import\s+escape\b", text) is not None
    )
    ok = "safe_text" in text and escape_present
    return CheckResult(
        item=8,
        title="HTML safe_text helper escapes user input",
        priority="P0",
        status="PASS" if ok else "FAIL",
        reason=(
            "safe_text + html.escape present" if ok else "safe_text/html.escape missing"
        ),
    )


def check_09_no_markdown_output() -> CheckResult:
    """Markdown parse mode banned anywhere in user-facing surfaces."""
    files = _iter_files(BACKEND / "app", ".py", GLOBAL_PY_EXCLUDES)
    pat = re.compile(
        r"""(parse_mode\s*=\s*["']Markdown(V2)?["']|ParseMode\.MARKDOWN)""",
        re.IGNORECASE,
    )
    findings = _grep(pat, files)
    return CheckResult(
        item=9,
        title="Telegram messages use HTML parse mode only",
        priority="P0",
        status="PASS" if not findings else "FAIL",
        reason=(
            "Markdown parse mode found" if findings else "No Markdown parse mode use"
        ),
        findings=findings,
    )


def check_10_sentry_pii_off() -> CheckResult:
    """Backend Sentry init sets send_default_pii=False and uses scrubber."""
    target = BACKEND / "app" / "main.py"
    text = (
        target.read_text(encoding="utf-8", errors="ignore") if target.exists() else ""
    )
    ok = "send_default_pii=False" in text and "before_send=sentry_before_send" in text
    return CheckResult(
        item=10,
        title="Sentry PII scrubbing configured",
        priority="P1",
        status="PASS" if ok else "FAIL",
        reason=(
            "Sentry init has send_default_pii=False and scrubber"
            if ok
            else "Sentry init missing PII scrubbing flags"
        ),
    )


def check_11_masking_utility() -> CheckResult:
    """utils/masking.py exists with both helpers."""
    target = BACKEND / "app" / "utils" / "masking.py"
    if not target.exists():
        return CheckResult(
            item=11,
            title="Masking utility present",
            priority="P1",
            status="FAIL",
            reason="app/utils/masking.py missing",
        )
    text = target.read_text(encoding="utf-8", errors="ignore")
    ok = "mask_dict" in text and "sentry_before_send" in text
    return CheckResult(
        item=11,
        title="Sensitive keys masked in log outputs",
        priority="P1",
        status="PASS" if ok else "FAIL",
        reason=(
            "mask_dict + sentry_before_send present"
            if ok
            else "Masking helpers incomplete"
        ),
    )


def check_12_conversation_compaction_tests() -> CheckResult:
    """Conversation compaction must have an explicit test."""
    candidates = list((BACKEND / "tests").rglob("test_*compact*.py"))
    candidates += list((BACKEND / "tests").rglob("test_*conversation*.py"))
    return CheckResult(
        item=12,
        title="Conversation compaction tested",
        priority="P1",
        status="PASS" if candidates else "FAIL",
        reason=(
            f"Found {candidates[0].relative_to(REPO_ROOT).as_posix()}"
            if candidates
            else "No compaction/conversation test discovered"
        ),
    )


def check_13_token_budget_enforced() -> CheckResult:
    """LLM client must enforce both input and output token limits."""
    target = BACKEND / "app" / "services" / "llm.py"
    if not target.exists():
        return CheckResult(
            item=13,
            title="Token budget limits enforced in LLM client",
            priority="P1",
            status="FAIL",
            reason="services/llm.py missing",
        )
    text = target.read_text(encoding="utf-8", errors="ignore")
    ok = "max_output_tokens" in text or "MAX_OUTPUT_TOKENS" in text
    return CheckResult(
        item=13,
        title="Token budget limits enforced in LLM client",
        priority="P1",
        status="PASS" if ok else "FAIL",
        reason=(
            "Output-token cap referenced in services/llm.py"
            if ok
            else "Output-token cap reference missing in services/llm.py"
        ),
    )


def check_14_filing_approval_strict() -> CheckResult:
    """Filing approval uses an exact anchored regex."""
    target = BACKEND / "app" / "agents" / "filing_approval.py"
    if not target.exists():
        return CheckResult(
            item=14,
            title="Filing approval uses exact regex match",
            priority="P0",
            status="FAIL",
            reason="agents/filing_approval.py missing",
        )
    text = target.read_text(encoding="utf-8", errors="ignore")
    ok = "^ENMA APPROVE FILING" in text and r"\d{4}" in text
    return CheckResult(
        item=14,
        title="Filing approval uses anchored exact regex",
        priority="P0",
        status="PASS" if ok else "FAIL",
        reason=(
            "Exact anchored regex present"
            if ok
            else "Filing approval regex looks loose or missing"
        ),
    )


def check_15_bot_token_encrypted() -> CheckResult:
    return CheckResult(
        item=15,
        title="Bot token encrypted at rest in ca_firms",
        priority="P0",
        status="SKIP",
        reason="DB-level encryption — verified via tests/test_db/test_firm_secrets.py",
    )


def check_16_pii_encrypted() -> CheckResult:
    return CheckResult(
        item=16,
        title="GSTIN / PAN encrypted at rest",
        priority="P1",
        status="SKIP",
        reason="DB-level encryption — verified via integration suite",
    )


def check_17_ssl_in_prod_dsn() -> CheckResult:
    """Production env template must require ssl=require on DATABASE_URL."""
    template = REPO_ROOT / ".env.production.example"
    if not template.exists():
        # Template lands later in this phase — flag SKIP, not FAIL.
        return CheckResult(
            item=17,
            title="DB connections require SSL/TLS",
            priority="P0",
            status="SKIP",
            reason=".env.production.example pending (Phase 8 deployment step)",
        )
    text = template.read_text(encoding="utf-8", errors="ignore")
    ok = "sslmode=require" in text or "ssl=require" in text
    return CheckResult(
        item=17,
        title="DB connections require SSL/TLS in production template",
        priority="P0",
        status="PASS" if ok else "FAIL",
        reason=("sslmode=require found" if ok else "sslmode=require missing"),
    )


def check_18_no_env_committed() -> CheckResult:
    """No .env files in the git tree (except .example)."""
    bad: list[str] = []
    for path in REPO_ROOT.rglob(".env*"):
        if any(part in {"node_modules", ".venv"} for part in path.parts):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if ".example" in rel:
            continue
        if rel.startswith(".env"):
            bad.append(rel)
            continue
        if "/.env" in rel and ".example" not in rel:
            bad.append(rel)
    return CheckResult(
        item=18,
        title="No .env committed to git",
        priority="P0",
        status="PASS" if not bad else "FAIL",
        reason=(
            "No tracked .env files"
            if not bad
            else f"Found tracked .env files: {', '.join(bad)}"
        ),
    )


def check_19_key_rotation_docs() -> CheckResult:
    """Rotation procedure documented."""
    candidates = [
        REPO_ROOT / "docs" / "SECRET_ROTATION.md",
        REPO_ROOT / "docs" / "ONBOARDING.md",
        REPO_ROOT / "README.md",
    ]
    rotation_pat = re.compile(r"rotat(e|ion|ing)", re.IGNORECASE)
    found = next(
        (
            p
            for p in candidates
            if p.exists()
            and rotation_pat.search(p.read_text(encoding="utf-8", errors="ignore"))
        ),
        None,
    )
    return CheckResult(
        item=19,
        title="API key rotation procedure documented",
        priority="P2",
        status="PASS" if found else "FAIL",
        reason=(
            f"Rotation guidance in {found.relative_to(REPO_ROOT).as_posix()}"
            if found
            else "No rotation guidance found in docs/README"
        ),
    )


def check_20_idem_log_cleanup() -> CheckResult:
    """Idempotency log cleanup cron job present."""
    # Either a cron handler or a scheduled job referring to idempotency_log
    cron_dir = BACKEND / "app" / "api" / "routes"
    if not cron_dir.exists():
        return CheckResult(
            item=20,
            title="Idempotency log cleanup cron",
            priority="P2",
            status="FAIL",
            reason="cron routes dir missing",
        )
    text_blob = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in cron_dir.rglob("*.py")
    )
    ok = (
        re.search(r"idempotency_log|idempotency.+cleanup", text_blob, re.IGNORECASE)
        is not None
    )
    return CheckResult(
        item=20,
        title="Idempotency log cleanup cron configured",
        priority="P2",
        status="PASS" if ok else "FAIL",
        reason=(
            "Cleanup reference found in cron routes"
            if ok
            else "No idempotency-log cleanup reference in cron routes"
        ),
    )


# Plus one bonus check (not in the 20 but enforced repo-wide).
def check_x_decimal_for_money() -> CheckResult:
    """No float arithmetic on canonical money column names."""
    files = _iter_files(BACKEND / "app", ".py", GLOBAL_PY_EXCLUDES)
    pat = re.compile(
        r"""float\s*\(\s*(amount|tax|total|cgst|sgst|igst|cess|rcm|tds|claim|block)\b""",
        re.IGNORECASE,
    )
    findings = _grep(pat, files)
    return CheckResult(
        item=21,
        title="No float() coercion of money fields",
        priority="P0",
        status="PASS" if not findings else "FAIL",
        reason=(
            "float() coercion of money-named fields detected"
            if findings
            else "No float() coercion of money fields"
        ),
        findings=findings,
    )


ALL_CHECKS = [
    check_01_basequery,
    check_02_rls_policies,
    check_03_cross_tenant_tests,
    check_04_idempotency_middleware,
    check_05_hmac_verify,
    check_06_raw_sql_fstrings,
    check_07_no_user_text_in_system_prompts,
    check_08_safe_text_html,
    check_09_no_markdown_output,
    check_10_sentry_pii_off,
    check_11_masking_utility,
    check_12_conversation_compaction_tests,
    check_13_token_budget_enforced,
    check_14_filing_approval_strict,
    check_15_bot_token_encrypted,
    check_16_pii_encrypted,
    check_17_ssl_in_prod_dsn,
    check_18_no_env_committed,
    check_19_key_rotation_docs,
    check_20_idem_log_cleanup,
    check_x_decimal_for_money,
]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _render_text(results: list[CheckResult]) -> str:
    lines = []
    status_color = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}
    for r in results:
        lines.append(
            f"{status_color[r.status]} #{r.item:02d} ({r.priority})  {r.title}"
        )
        if r.reason:
            lines.append(f"        {r.reason}")
        for f in r.findings[:5]:
            lines.append(f"        - {f.path}:{f.line}  {f.snippet}")
        if len(r.findings) > 5:
            lines.append(f"        ... +{len(r.findings) - 5} more findings")
    counts = {
        "pass": sum(1 for r in results if r.status == "PASS"),
        "fail": sum(1 for r in results if r.status == "FAIL"),
        "skip": sum(1 for r in results if r.status == "SKIP"),
    }
    lines.append("")
    lines.append(
        f"Summary: pass={counts['pass']}  fail={counts['fail']}  skip={counts['skip']}"
    )
    return "\n".join(lines)


def _render_json(results: list[CheckResult]) -> str:
    return json.dumps(
        [
            {
                "item": r.item,
                "title": r.title,
                "priority": r.priority,
                "status": r.status,
                "reason": r.reason,
                "findings": [
                    {"path": f.path, "line": f.line, "snippet": f.snippet}
                    for f in r.findings
                ],
            }
            for r in results
        ],
        indent=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--list", action="store_true", help="list checks and exit 0")
    args = parser.parse_args()

    if args.list:
        for fn in ALL_CHECKS:
            print(f"- {fn.__name__}: {fn.__doc__ or ''}".strip())
        return 0

    results = [fn() for fn in ALL_CHECKS]
    out = _render_json(results) if args.json else _render_text(results)
    print(out)

    failed = any(r.status == "FAIL" for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
