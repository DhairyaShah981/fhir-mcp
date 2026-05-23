"""CLI: ``fhir-mcp serve | eval | reid``."""

from __future__ import annotations

import asyncio

import structlog
import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import get_settings

app = typer.Typer(
    name="fhir-mcp",
    help="The trustworthy FHIR bridge for AI agents.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
log = structlog.get_logger(__name__)


@app.command()
def version() -> None:
    """Print the version and exit."""
    console.print(f"fhir-mcp {__version__}")


@app.command()
def serve(
    transport: str = typer.Option("stdio", help="MCP transport: stdio | sse"),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8765),
) -> None:
    """Start the MCP server."""
    from .server import run_sse, run_stdio

    if transport == "stdio":
        run_stdio()
    elif transport == "sse":
        run_sse(host=host, port=port)
    else:
        raise typer.BadParameter(f"Unknown transport: {transport}")


@app.command()
def config_show() -> None:
    """Print the effective configuration (with secrets masked)."""
    s = get_settings()
    t = Table(title="fhir-mcp configuration")
    t.add_column("Setting")
    t.add_column("Value")
    rows = {
        "backend": s.backend,
        "hapi_base_url": s.hapi_base_url,
        "state_dir": str(s.state_dir),
        "audit_url": s.resolved_audit_url(),
        "vault_url": s.vault_url(),
        "enable_reid": str(s.enable_reid),
        "reid_key_set": "yes" if s.reid_key else "no",
        "supabase_configured": "yes" if s.supabase_url else "no",
        "langfuse_enabled": "yes" if s.langfuse_enabled() else "no",
        "cds_hooks_url": s.cds_hooks_url,
        "terminology_url": s.terminology_url,
        "verbose_audit": str(s.verbose_audit),
    }
    for k, v in rows.items():
        t.add_row(k, v)
    console.print(t)


@app.command(name="reid")
def reid_cmd(
    pseudonym: str = typer.Argument(..., help="Pseudonym to resolve (e.g. PT_a1b2c3)."),
    reason: str = typer.Option("", help="Justification text — written to the audit row."),
) -> None:
    """Resolve a pseudonym to the original PHI value. Audited."""
    from .deid.reid import (
        ReidDisabledError,
        ReidNotFoundError,
        ReidUnauthorizedError,
        reidentify,
    )

    s = get_settings()
    key = s.reid_key or ""

    async def _go() -> str:
        return await reidentify(pseudonym, key=key, actor="cli", reason=reason)

    try:
        original = asyncio.run(_go())
    except ReidDisabledError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except ReidUnauthorizedError as exc:
        console.print(f"[red]{exc}[/red] Set FHIR_MCP_REID_KEY in your env.")
        raise typer.Exit(code=3) from exc
    except ReidNotFoundError:
        console.print(f"[yellow]No vault entry for {pseudonym}[/yellow]")
        raise typer.Exit(code=4) from None

    console.print(f"{pseudonym} → [bold]{original}[/bold]")


@app.command(name="eval")
def eval_cmd(
    report_path: str = typer.Option("eval_report.md", help="Where to write the scoreboard."),
) -> None:
    """Run the eval suite and render the scoreboard."""
    import subprocess

    rc = subprocess.call(["pytest", "evals", "-v", "-m", "eval"])
    if rc != 0:
        console.print(f"[yellow]pytest exited non-zero ({rc}); rendering partial report[/yellow]")
    from evals.report import render_report

    render_report(report_path)
    console.print(f"[green]Wrote {report_path}[/green]")
