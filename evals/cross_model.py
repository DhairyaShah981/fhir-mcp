"""Cross-model judge harness.

Runs the same rubric through multiple LLM judges and reports score deltas.
This answers the question "is our scoreboard model-dependent?" — a credible
answer is part of how we make the eval suite trustworthy.

Supported judges (all opt-in via env vars):
  * Claude — ``ANTHROPIC_API_KEY``
  * OpenAI — ``OPENAI_API_KEY``
  * Gemini — ``GEMINI_API_KEY``

Usage::

    uv run python -m evals.cross_model --scenario primary_diagnosis

Skipped silently when no API keys are present; emits a JSON report to
``cross_model_report.json`` so CI can attach it as an artifact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "evals" / "golden"
JUDGE = REPO / "evals" / "judge"


@dataclass
class JudgeResult:
    model: str
    score: int
    rationale: str
    raw: str


async def _claude_judge(prompt: str) -> JudgeResult | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("FHIR_MCP_EVAL_JUDGE_MODEL", "claude-sonnet-4-6"),
        max_tokens=800,
        system="You are a clinical-informatics judge. Return only the JSON specified.",
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text  # type: ignore[union-attr]
    return _parse_verdict("claude", text)


async def _openai_judge(prompt: str) -> JudgeResult | None:
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx is always installed
        return None
    async with httpx.AsyncClient(timeout=60.0) as c:
        resp = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.environ.get("FHIR_MCP_OPENAI_JUDGE_MODEL", "gpt-4o-mini"),
                "messages": [
                    {"role": "system", "content": "You are a clinical-informatics judge. Return only JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
            },
        )
        if resp.status_code != 200:
            return None
        text = resp.json()["choices"][0]["message"]["content"]
    return _parse_verdict("openai", text)


async def _gemini_judge(prompt: str) -> JudgeResult | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return None
    model = os.environ.get("FHIR_MCP_GEMINI_JUDGE_MODEL", "gemini-1.5-pro")
    async with httpx.AsyncClient(timeout=60.0) as c:
        resp = await c.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0},
            },
        )
        if resp.status_code != 200:
            return None
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_verdict("gemini", text)


def _parse_verdict(model: str, raw: str) -> JudgeResult:
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").rstrip("`")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return JudgeResult(model=model, score=0, rationale="unparseable", raw=raw)
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return JudgeResult(model=model, score=0, rationale="invalid-json", raw=raw)
    return JudgeResult(
        model=model,
        score=int(data.get("score", 0)),
        rationale=str(data.get("rationale", "")),
        raw=raw,
    )


async def run_scenario(scenario: str, pseudonym: str) -> dict[str, Any]:
    from fhir_mcp.tools.get_patient_summary import (
        GetPatientSummaryInput,
        get_patient_summary,
    )

    rubric_path = JUDGE / f"{scenario}.md"
    if not rubric_path.exists():
        raise FileNotFoundError(f"Rubric missing: {rubric_path}")
    summary = await get_patient_summary(GetPatientSummaryInput(patient_pseudonym=pseudonym))
    prompt = (
        rubric_path.read_text()
        + "\n\n---\n\n## Structured summary to judge\n```json\n"
        + json.dumps(summary.model_dump(mode="json"), indent=2)
        + "\n```\n"
    )
    judges = await asyncio.gather(
        _claude_judge(prompt),
        _openai_judge(prompt),
        _gemini_judge(prompt),
        return_exceptions=False,
    )
    results = [r for r in judges if r is not None]
    if not results:
        return {"scenario": scenario, "skipped": True, "reason": "no judge API keys configured"}
    scores = [r.score for r in results]
    return {
        "scenario": scenario,
        "pseudonym": pseudonym,
        "judges": [
            {"model": r.model, "score": r.score, "rationale": r.rationale} for r in results
        ],
        "spread": max(scores) - min(scores),
        "min": min(scores),
        "max": max(scores),
        "median": sorted(scores)[len(scores) // 2],
    }


async def _main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", default="primary_diagnosis")
    p.add_argument("--pseudonym", default="patient-diabetic-60yo")
    p.add_argument("--out", default="cross_model_report.json")
    args = p.parse_args()
    report = await run_scenario(args.scenario, args.pseudonym)
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
