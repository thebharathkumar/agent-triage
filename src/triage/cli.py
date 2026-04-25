"""cli.py - entry point for the triage CLI."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from triage.comparer import compare_event_batches
from triage.grouper import group_events
from triage.loader import load_files
from triage.reporter import build_comparison_report, build_report
from triage.scorer import score_patterns


@click.group()
@click.version_option(package_name="triage")
def main() -> None:
    """Analyze agent trace files.

    Run a morning severity report or compare two batches of runs to see
    what got better or worse between them.
    """


@main.command("report")
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
def report(files: tuple[Path, ...], output: Path | None, top: int) -> None:
    """Produce a ranked morning severity report from one or more trace files.

    Pass one or more NDJSON trace files as arguments. Glob expansion is
    handled by your shell, so you can do:

        triage report runs/phase4/*.ndjson

    The report goes to stdout by default. Use --output to write to a file.
    """
    if not files:
        click.echo(
            "Error: provide at least one NDJSON file. "
            "Example: triage report runs/phase4/events_seed42.ndjson",
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

    patterns = group_events(result.events)
    run_ids = {e.run_id for e in result.events}
    total_runs = len(run_ids)

    scored = score_patterns(patterns, result.events, total_runs)

    text = build_report(
        scored=scored,
        total_runs=total_runs,
        total_patterns=len(patterns),
        source_files=[str(f) for f in files],
        top_n=top,
    )

    _emit(text, output)


@main.command("compare")
@click.argument("before", type=click.Path(exists=True, path_type=Path))
@click.argument("after", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Write comparison report to this file instead of stdout.",
)
def compare(before: Path, after: Path, output: Path | None) -> None:
    """Diff two batches of trace events.

    Reports per-classification frequency, unrecovered-count, and
    recovery-latency deltas, new patterns that appeared only in `after`,
    patterns resolved between `before` and `after`, and changes in
    patterns that persisted across both.

    Each argument can be a single .ndjson file or a directory; in the
    directory case every `.ndjson` file directly inside it is loaded
    (non-recursively). This makes the command pipeline-friendly:

        triage compare runs/before/ runs/after/
        triage compare runs/before.ndjson runs/after.ndjson
    """
    before_files = _expand_to_ndjson_paths(before)
    after_files = _expand_to_ndjson_paths(after)

    if not before_files or not after_files:
        click.echo(
            "No .ndjson files found in one or both inputs.", err=True
        )
        sys.exit(1)

    before_result = load_files(before_files)
    after_result = load_files(after_files)

    for err in before_result.parse_errors + after_result.parse_errors:
        click.echo(f"[parse error] {err}", err=True)

    if not before_result.events or not after_result.events:
        click.echo("No events loaded from one or both inputs.", err=True)
        sys.exit(1)

    comparison = compare_event_batches(
        before_result.events, after_result.events
    )

    text = build_comparison_report(
        comparison=comparison,
        before_path=_format_path_label(before, before_files),
        after_path=_format_path_label(after, after_files),
    )

    _emit(text, output)


def _expand_to_ndjson_paths(path: Path) -> list[Path]:
    """Resolve a CLI path to a sorted list of ndjson files.

    Files map to themselves; directories expand to their direct
    `*.ndjson` children. Sorting keeps the output deterministic.
    """
    if path.is_dir():
        return sorted(path.glob("*.ndjson"))
    return [path]


def _format_path_label(original: Path, expanded: list[Path]) -> str:
    if original.is_dir():
        return f"{original}/ ({len(expanded)} file(s))"
    return str(original)


def _emit(text: str, output: Path | None) -> None:
    if output is None:
        click.echo(text)
        return
    try:
        output.write_text(text, encoding="utf-8")
        click.echo(f"Report written to {output}", err=True)
    except OSError as exc:
        click.echo(f"Error writing to {output}: {exc}", err=True)
        sys.exit(1)
