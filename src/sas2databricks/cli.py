"""`s2db` command-line interface."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__
from .llm import CopilotProvider, Model, NullProvider, provider_from_env
from .pipeline import MigrationResult, migrate_file
from .project import ALL_TARGETS, migrate_all_targets, migrate_project
from .project import BUNDLE_NOTEBOOK_TARGETS as _BUNDLE_NOTEBOOK_TARGETS


def _force_utf8() -> None:
    """Ensure non-ASCII (report tables, notes) print on legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


_force_utf8()
console = Console()

_MODEL_CHOICE = click.Choice([m.value for m in Model], case_sensitive=False)
_TARGET_CHOICE = click.Choice(
    ["pyspark", "sparksql", "dlt", "workflow", "validate", "bundle"], case_sensitive=False
)
# `migrate` adds "all" (the default): emit PySpark + Spark SQL + DLT in one command.
_TARGET_CHOICE_MIGRATE = click.Choice(
    ["all", "pyspark", "sparksql", "dlt", "workflow", "validate", "bundle"], case_sensitive=False
)


def _resolve_provider(copilot: bool):
    """Pick a provider: --copilot wins, else an env-configured API provider, else stub."""
    if copilot:
        return CopilotProvider()
    return provider_from_env() or NullProvider()


@click.group(help="sas2databricks — migrate SAS analytics, transforms & reports to Databricks.")
@click.version_option(__version__, prog_name="s2db")
def main() -> None:  # pragma: no cover - entry point
    pass


@main.command(help="Convert a single SAS file and print (or write) the result.")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--target", "-t", type=_TARGET_CHOICE, default="pyspark", show_default=True)
@click.option("--model", "-m", type=_MODEL_CHOICE, default="opus-4.8", show_default=True,
              help="LLM used for low-confidence steps.")
@click.option("--out", "-o", type=click.Path(path_type=Path), default=None,
              help="Write output here instead of stdout.")
@click.option("--copilot", is_flag=True,
              help="Delegate low-confidence steps to the host Copilot model.")
@click.option("--threshold", type=float, default=0.8, show_default=True)
@click.option("--catalog", default=None, help="Unity Catalog catalog (dlt target).")
@click.option("--schema", default=None, help="Unity Catalog schema (dlt target).")
@click.option("--ref-base", default=None, help="Reference dataset folder (validate target).")
def convert(
    source: Path, target: str, model: str, out: Path | None, copilot: bool, threshold: float,
    catalog: str | None, schema: str | None, ref_base: str | None,
) -> None:
    provider = _resolve_provider(copilot)
    options = _emit_options(catalog, schema, ref_base)
    result = migrate_file(
        source, target=target, model=model, provider=provider, threshold=threshold, **options
    )
    if out:
        out.write_text(result.code, encoding="utf-8")
        console.print(f"[green]Wrote[/] {out}")
    else:
        click.echo(result.code)
    _print_summary(result)


@main.command(help="Migrate a whole SAS project (directory of .sas files).")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--target", "-t", type=_TARGET_CHOICE_MIGRATE, default="all", show_default=True,
              help="Databricks target. 'all' (default) emits PySpark + Spark SQL + DLT in one run.")
@click.option("--model", "-m", type=_MODEL_CHOICE, default="opus-4.8", show_default=True)
@click.option("--out", "-o", type=click.Path(path_type=Path), default=Path("out"),
              show_default=True)
@click.option("--copilot", is_flag=True,
              help="Delegate low-confidence steps to the host Copilot model.")
@click.option("--threshold", type=float, default=0.8, show_default=True)
@click.option("--catalog", default=None, help="Unity Catalog catalog (dlt target).")
@click.option("--schema", default=None, help="Unity Catalog schema (dlt target).")
@click.option("--ref-base", default=None, help="Reference dataset folder (validate target).")
@click.option("--html", is_flag=True, help="Also write HTML reports (per file + index).")
@click.option("--bundle", "bundle", is_flag=True,
              help="Assemble a deployable Databricks Asset Bundle "
                   "(databricks.yml + src/ notebooks + reports/).")
def migrate_cmd(
    source: Path, target: str, model: str, out: Path, copilot: bool, threshold: float,
    catalog: str | None, schema: str | None, ref_base: str | None, html: bool, bundle: bool,
) -> None:
    files = [source] if source.is_file() else sorted(source.rglob("*.sas"))
    if not files:
        console.print("[red]No .sas files found.[/]")
        raise SystemExit(1)

    def _print(f: Path, result: MigrationResult, dest: str) -> None:
        console.print(f"[green]OK[/] {f.name} -> {dest}  "
                      f"({result.review_count} step(s) need review)")

    options = _emit_options(catalog, schema, ref_base)
    provider = _resolve_provider(copilot)

    if target == "all":
        def _header(tgt: str) -> None:
            console.print(f"\n[bold cyan]== {tgt} ==[/]")

        multi = migrate_all_targets(
            files, out, targets=ALL_TARGETS, model=model, provider=provider,
            threshold=threshold, html=html, bundle=bundle, on_file=_print, on_target=_header,
            **options,
        )
        console.print(
            f"\n[bold]Migrated {multi.file_count} file(s) to {len(multi.targets)} targets[/] "
            f"({', '.join(multi.targets)}) -> {out}/  "
            f"| {multi.review_count} step(s) flagged for review"
        )
        console.print(f"Combined index: [cyan]{out / 'index.md'}[/]")
        if bundle:
            console.print("[bold]Bundles ready.[/] Each target folder has a databricks.yml -> "
                          f"[cyan]cd {out}/<target> && databricks bundle deploy -t dev[/]")
        else:
            console.print("Tip: narrow to one format with [cyan]-t pyspark[/], or add "
                          "[cyan]--bundle[/] for deployable bundles.")
        return

    if bundle and target not in _BUNDLE_NOTEBOOK_TARGETS:
        console.print(
            f"[red]--bundle needs a notebook target[/] "
            f"({', '.join(sorted(_BUNDLE_NOTEBOOK_TARGETS))}); got '{target}'."
        )
        raise SystemExit(1)

    project = migrate_project(
        files, out, target=target, model=model, provider=provider,
        threshold=threshold, html=html, bundle=bundle, on_file=_print, **options,
    )

    console.print(f"\n[bold]Migrated {project.file_count} file(s)[/] -> {out}/  "
                  f"| {project.review_count} step(s) flagged for review")
    if bundle:
        console.print("[bold]Bundle ready.[/] Deploy with: "
                      f"[cyan]databricks bundle deploy -t dev[/] (from {out}/)")


# register under the friendlier name `migrate`
main.add_command(migrate_cmd, name="migrate")


@main.command(help="Parse a SAS file and show the detected steps (no conversion).")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def parse_cmd(source: Path) -> None:
    from .parser import parse as parse_sas

    parsed = parse_sas(source.read_text(encoding="utf-8"), source_path=str(source))
    table = Table(title=f"Steps in {source.name}")
    table.add_column("#", justify="right")
    table.add_column("Kind")
    table.add_column("Proc")
    table.add_column("Lines")
    for i, step in enumerate(parsed.steps, 1):
        table.add_row(str(i), step.kind, step.proc or "-", f"{step.start_line}-{step.end_line}")
    console.print(table)
    if parsed.macro_vars:
        console.print(f"[dim]macro vars:[/] {parsed.macro_vars}")


main.add_command(parse_cmd, name="parse")


@main.command(help="Show the migration report for a SAS file (review hotspots).")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--target", "-t", type=_TARGET_CHOICE, default="pyspark", show_default=True)
@click.option("--model", "-m", type=_MODEL_CHOICE, default="opus-4.8", show_default=True)
@click.option("--html", "html_out", type=click.Path(path_type=Path), default=None,
              help="Write an HTML report to this path instead of markdown to stdout.")
def report(source: Path, target: str, model: str, html_out: Path | None) -> None:
    result = migrate_file(source, target=target, model=model)
    if html_out:
        html_out.write_text(result.report_html(), encoding="utf-8")
        console.print(f"[green]Wrote[/] {html_out}")
    else:
        click.echo(result.report_markdown())


@main.command(help="Run the MCP server (stdio) so GitHub Copilot can call the tools.")
def mcp() -> None:
    try:
        from .mcp.server import run
    except ImportError as exc:
        console.print("[red]MCP extras not installed.[/] Run: pip install \"sas2databricks[mcp]\"")
        raise SystemExit(1) from exc
    run()


def _print_summary(result: MigrationResult) -> None:
    review = result.review_count
    color = "yellow" if review else "green"
    console.print(
        f"[{color}]model={result.model.value} target={result.target} "
        f"steps={len(result.reports)} review={review} "
        f"llm_escalations={len(result.llm_requests)}[/]"
    )


def _emit_options(
    catalog: str | None, schema: str | None, ref_base: str | None
) -> dict[str, str]:
    options: dict[str, str] = {}
    if catalog:
        options["catalog"] = catalog
    if schema:
        options["schema"] = schema
    if ref_base:
        options["ref_base"] = ref_base
    return options


if __name__ == "__main__":  # pragma: no cover
    main()
