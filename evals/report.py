"""Render the eval scoreboard as Markdown for the README.

Runs each eval suite via a node-id selector so the scoreboard reflects the
actual per-suite counts. CDS Hooks + code validation suites ship in M2; until
then they're surfaced as ``pending``.
"""

from __future__ import annotations

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

_SUMMARY_PATTERN = re.compile(
    r"(?:(?P<passed>\d+)\s+passed)?(?:,\s+(?P<skipped>\d+)\s+skipped)?"
    r"(?:,\s+(?P<failed>\d+)\s+failed)?(?:,\s+(?P<errors>\d+)\s+errors?)?"
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(selector: str) -> tuple[int, int, str]:
    """Return ``(passed, total, status)`` for a pytest selector."""
    cmd = [sys.executable, "-m", "pytest", selector, "-q", "--tb=no", "--no-header"]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    output = proc.stdout + "\n" + proc.stderr
    summary_line = next(
        (
            line
            for line in reversed(output.splitlines())
            if "passed" in line or "failed" in line or "error" in line
        ),
        "",
    )
    if not summary_line:
        return 0, 0, "no-tests"
    m = _SUMMARY_PATTERN.search(summary_line)
    if not m:
        return 0, 0, "unparsed"
    passed = int(m.group("passed") or 0)
    skipped = int(m.group("skipped") or 0)
    failed = int(m.group("failed") or 0)
    errors = int(m.group("errors") or 0)
    total = passed + failed + errors
    if failed or errors:
        status = "fail"
    elif total == 0 and skipped > 0:
        status = "skipped"
    elif total == 0:
        status = "pending"
    else:
        status = "pass"
    return passed, total, status


def render_report(out_path: str) -> None:
    today = datetime.date.today().isoformat()
    suites = [
        ("Clinical accuracy", "evals/test_clinical_accuracy.py"),
        ("De-id leakage", "evals/test_deid.py"),
        ("Code validation", "evals/test_code_validation.py"),
        ("CDS Hooks correctness", "evals/test_cds_hooks.py"),
    ]
    rows = []
    for label, selector in suites:
        target = REPO_ROOT / selector
        if not target.exists():
            rows.append((label, "_pending_ (M2)", today))
            continue
        passed, total, status = _run(selector)
        if status == "pass":
            cell = f"{passed}/{total} ✅"
        elif status == "fail":
            cell = f"{passed}/{total} ❌"
        elif status == "skipped":
            cell = "skipped"
        else:
            cell = "_pending_"
        rows.append((label, cell, today))

    lines = [
        "# fhir-mcp eval scoreboard",
        "",
        f"_Generated {today}_",
        "",
        "| Eval                   | Score        | Last run   |",
        "|------------------------|--------------|------------|",
    ]
    lines += [f"| {label:<22} | {cell:<12} | {date}  |" for label, cell, date in rows]
    Path(out_path).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="eval_report.md")
    args = p.parse_args()
    render_report(args.out)
