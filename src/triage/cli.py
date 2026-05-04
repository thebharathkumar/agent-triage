"""cli.py - entry point for the triage CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from triage.config import TriageConfig
from triage.grouper import group_events
from triage.loader import load_files
from triage.reporter import build_report
from triage.scorer import score_patterns


@click.command()
@click.argument("files", nargs=-1, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Write report to this file instead of stdout.",
)
@click.option(
    "--top",
    "-n",
    type=click.IntRange(min=1),
    default=3,
    show_default=True,
    help="Number of top incidents to include in the report.",
)
@click.option(
    "--ai-analysis",
    is_flag=True,
    default=False,
    help="Enrich each top incident with a Claude-generated root-cause narrative.",
)
@click.option(
    "--api-key",
    default=None,
    envvar="ANTHROPIC_API_KEY",
    help="Anthropic API key (defaults to ANTHROPIC_API_KEY env var).",
    show_envvar=True,
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a triage.toml config file (overrides scoring weights, etc.).",
)
@click.version_option(package_name="agent-triage")
def main(
    files: tuple[Path, ...],
    output: Path | None,
    top: int,
    ai_analysis: bool,
    api_key: str | None,
    config_path: Path | None,
) -> None:
    """Analyze agent trace files and produce a morning severity report.

    Pass one or more NDJSON trace files as arguments. Glob expansion is
    handled by your shell, so you can do:

        triage runs/phase4/*.ndjson

    The report goes to stdout by default. Use --output to write to a file.
    """
    if not files:
        click.echo(
            "Error: provide at least one NDJSON file. "
            "Example: triage runs/phase4/events_seed42.ndjson",
            err=True,
        )
        sys.exit(1)

    result = load_files(list(files))

    if result.parse_errors:
        for err in result.parse_errors:
            click.echo(f"[parse error] {err}", err=True)

    if not result.events:
        click.echo("No events loaded. Check your input files.", err=True)
        sys.exit(1)

    cfg = TriageConfig.from_file(config_path) if config_path else TriageConfig.default()

    patterns = group_events(result.events)

    # Count distinct run IDs
    run_ids = {e.run_id for e in result.events}
    total_runs = len(run_ids)

    scored = score_patterns(patterns, result.events, total_runs, config=cfg.scoring)

    analyses = None
    if ai_analysis:
        from triage.analyst import analyze_patterns

        try:
            click.echo("Running AI root-cause analysis...", err=True)
            analyses = analyze_patterns(scored, top_n=top, api_key=api_key)
        except RuntimeError as exc:
            click.echo(f"[ai-analysis] {exc}", err=True)
            sys.exit(1)

    report = build_report(
        scored=scored,
        total_runs=total_runs,
        total_patterns=len(patterns),
        source_files=[str(f) for f in files],
        top_n=top,
        analyses=analyses,
    )

    if output:
        try:
            output.write_text(report, encoding="utf-8")
            click.echo(f"Report written to {output}", err=True)
        except OSError as exc:
            click.echo(f"Error writing to {output}: {exc}", err=True)
            sys.exit(1)
    else:
        click.echo(report)
