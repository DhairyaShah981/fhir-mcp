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


@app.command(name="smart-discover")
def smart_discover_cmd(
    issuer: str = typer.Argument(..., help="FHIR base URL of the SMART issuer."),
) -> None:
    """Fetch and pretty-print the issuer's .well-known/smart-configuration."""
    from .auth.smart import discover

    async def _go():
        return await discover(issuer)

    cfg = asyncio.run(_go())
    t = Table(title=f"SMART configuration — {issuer}")
    t.add_column("Field")
    t.add_column("Value")
    for k, v in {
        "issuer": cfg.issuer,
        "authorization_endpoint": cfg.authorization_endpoint,
        "token_endpoint": cfg.token_endpoint,
        "capabilities": ", ".join(cfg.capabilities),
        "scopes_supported": ", ".join(cfg.scopes_supported),
        "auth_methods": ", ".join(cfg.token_endpoint_auth_methods_supported),
    }.items():
        t.add_row(k, v)
    console.print(t)


@app.command(name="smart-authorize-url")
def smart_authorize_url_cmd(
    issuer: str | None = typer.Option(None, help="Override FHIR_MCP_SMART_ISSUER."),
    client_id: str | None = typer.Option(None, help="Override FHIR_MCP_SMART_CLIENT_ID."),
    redirect_uri: str | None = typer.Option(None, help="Override FHIR_MCP_SMART_REDIRECT_URI."),
    scopes: str | None = typer.Option(None, help="Override FHIR_MCP_SMART_SCOPES."),
) -> None:
    """Print the SMART authorize URL the user should visit to start the OAuth dance."""
    from .auth.smart import AuthorizationCodeFlow, discover

    s = get_settings()
    iss = issuer or s.smart_issuer
    cid = client_id or s.smart_client_id
    if not iss or not cid:
        console.print(
            "[red]Need FHIR_MCP_SMART_ISSUER and FHIR_MCP_SMART_CLIENT_ID (or --issuer/--client-id).[/red]"
        )
        raise typer.Exit(code=2)

    async def _go() -> tuple[str, str]:
        cfg = await discover(iss)
        flow = AuthorizationCodeFlow(
            config=cfg,
            client_id=cid,
            redirect_uri=redirect_uri or s.smart_redirect_uri,
            scopes=scopes or s.smart_scopes,
        )
        return flow.authorize_url(), flow.state

    url, state = asyncio.run(_go())
    console.print(f"[bold]Visit this URL to authorize:[/bold]\n{url}\n")
    console.print(f"[dim]Expected state on callback:[/dim] {state}")


@app.command(name="smart-jwt")
def smart_jwt_cmd(
    issuer: str | None = typer.Option(None, help="Issuer to derive the token endpoint from."),
) -> None:
    """Mint a SMART backend-services client assertion (for manual testing)."""
    from .auth.smart import _build_client_assertion, discover

    s = get_settings()
    iss = issuer or s.smart_issuer
    if not iss or not s.smart_client_id or not s.smart_private_key_pem or not s.smart_key_id:
        console.print(
            "[red]Need FHIR_MCP_SMART_ISSUER, _CLIENT_ID, _PRIVATE_KEY_PEM, and _KEY_ID set.[/red]"
        )
        raise typer.Exit(code=2)

    client_id = s.smart_client_id
    private_key_pem = s.smart_private_key_pem
    key_id = s.smart_key_id

    async def _go() -> str:
        cfg = await discover(iss)
        return _build_client_assertion(
            client_id=client_id,
            token_endpoint=cfg.token_endpoint,
            private_key_pem=private_key_pem,
            key_id=key_id,
        )

    console.print(asyncio.run(_go()))


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
