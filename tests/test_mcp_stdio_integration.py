"""End-to-end integration test: spawn ``fhir-mcp serve`` and drive it over stdio JSON-RPC.

This is the contract test that guards against log-output-contaminating-stdout
regressions (and similar transport-level bugs). Skipped on environments where
``uv`` isn't available.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv CLI not available")


async def _send(proc: asyncio.subprocess.Process, payload: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc: asyncio.subprocess.Process) -> dict:
    assert proc.stdout is not None
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
    assert line, "server closed stdout unexpectedly"
    return json.loads(line)


@pytest.mark.asyncio
async def test_stdio_end_to_end() -> None:
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "fhir-mcp", "serve",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        init = await _recv(proc)
        assert init["result"]["serverInfo"]["name"] == "fhir-mcp"

        await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools_resp = await _recv(proc)
        names = {t["name"] for t in tools_resp["result"]["tools"]}
        assert {
            "search_patients",
            "get_patient_summary",
            "search_observations",
            "search_conditions",
            "get_medications",
            "validate_code",
            "create_clinical_note",
            "run_cds_hook",
        }.issubset(names)

        await _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "search_patients",
                    "arguments": {"name": "Smith", "limit": 3},
                },
            },
        )
        call_resp = await _recv(proc)
        payload = json.loads(call_resp["result"]["content"][0]["text"])
        assert payload["count"] >= 1
        assert payload["patients"][0]["pseudonym"].startswith("PT_")
        # The display name must not contain raw PHI from the source bundle.
        assert "Jonathan" not in payload["patients"][0]["display_name"]
        assert "Smith" not in payload["patients"][0]["display_name"]

        await _send(proc, {"jsonrpc": "2.0", "id": 4, "method": "prompts/list"})
        prompts_resp = await _recv(proc)
        prompt_names = {p["name"] for p in prompts_resp["result"]["prompts"]}
        assert {"soap_note", "discharge_summary", "prior_auth_letter"}.issubset(prompt_names)

        await _send(proc, {"jsonrpc": "2.0", "id": 5, "method": "resources/templates/list"})
        res_resp = await _recv(proc)
        res_names = {r["name"] for r in res_resp["result"]["resourceTemplates"]}
        assert {"patient_summary", "lab_trends", "medications"}.issubset(res_names)
    finally:
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.terminate()
            await proc.wait()
