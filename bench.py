#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "plotly",
# ]
# ///
"""Run the real-world serde.zig benchmark and open an interactive results page.

The Zig programs already perform warmup and repeat each fixture according to
its size.  This driver runs each selected target in separate processes, keeps
the raw text for auditability, aggregates process runs by median, and renders
one interactive web page (``bench-results/index.html``) with grouped bar
charts for every format/mode and corpus file, then opens it in your browser.

Typical use::

    uv run bench.py                          # run everything, open the page
    uv run bench.py --runs 5 --mode generic  # five process runs, generic only
    uv run bench.py --format msgpack         # one format
    uv run bench.py --output csv --output md # also write summary.csv / summary.md
    python3 bench.py --plot-only             # rebuild the page from saved results

Raw per-process output and ``measurements.json`` are always kept under the
result directory (``bench-results`` by default) for auditability; the page and
the optional CSV/Markdown exports are derived from them. JSON decode
throughput uses the JSON input size; MessagePack encode uses the encoded
output size, matching the benchmark implementation.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "bench-results"
DATASETS = (
    "canada.json",
    "citm_catalog.json",
    "fgo.json",
    "github_events.json",
    "gsoc-2018.json",
    "lottie.json",
    "otfcc.json",
    "poet.json",
    "twitter.json",
    "twitterescaped.json",
)
TYPED_DATASETS = frozenset(
    {"canada.json", "github_events.json", "poet.json", "twitter.json", "twitterescaped.json"}
)
FORMATS = ("json", "msgpack")
MODES = ("generic", "typed")
OPERATIONS = ("decode", "encode")
FORMAT_LABELS = {"json": "JSON", "msgpack": "MessagePack"}

DATASET_RE = re.compile(
    r"^\s*(?P<dataset>\S+)\s+\((?P<input_bytes>\d+) bytes,\s+(?P<repeats>\d+) repeats\)\s*$"
)
METRIC_RE = re.compile(
    r"^\s*(?P<label>[^:]+):\s+"
    r"(?P<milliseconds>[0-9]+(?:\.[0-9]+)?)\s+ms/op,\s+"
    r"(?P<reported_mib>[0-9]+(?:\.[0-9]+)?)\s+MiB/s"
    r"(?:\s+\((?P<output_bytes>\d+) bytes\))?\s*$"
)


@dataclass(frozen=True)
class Measurement:
    """One metric line from one benchmark process."""

    format: str
    mode: str
    dataset: str
    operation: str
    input_bytes: int
    output_bytes: int | None
    milliseconds: float
    reported_mib_s: float
    run: int

    @property
    def measured_bytes(self) -> int:
        if self.operation == "encode" and self.output_bytes is not None:
            return self.output_bytes
        return self.input_bytes


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def selected_values(value: str, choices: Sequence[str], name: str) -> tuple[str, ...]:
    if value == "all":
        return tuple(choices)
    if value not in choices:
        valid = ", ".join((*choices, "all"))
        raise ValueError(f"invalid {name} {value!r}; choose from {valid}")
    return (value,)


def build_command(
    format_name: str,
    mode: str,
    zig: str,
    optimize: str | None,
    *,
    no_build: bool,
    binary_dir: Path,
) -> list[str]:
    if no_build:
        binary = binary_dir / f"{format_name}-bench"
        return [str(binary), mode]

    command = [zig, "build", f"bench-{format_name}", f"-Dmode={mode}"]
    if optimize:
        command.append(f"-Doptimize={optimize}")
    return command


def parse_output(output: str, format_name: str, mode: str, run: int) -> list[Measurement]:
    """Parse benchmark output without depending on stdout/stderr ordering."""

    current_dataset: str | None = None
    current_input_bytes: int | None = None
    measurements: list[Measurement] = []

    for line in output.splitlines():
        dataset_match = DATASET_RE.match(line)
        if dataset_match:
            current_dataset = dataset_match.group("dataset")
            current_input_bytes = int(dataset_match.group("input_bytes"))
            continue

        metric_match = METRIC_RE.match(line)
        if not metric_match:
            continue
        if current_dataset is None or current_input_bytes is None:
            raise RuntimeError(f"found metric before a dataset header: {line!r}")

        label = metric_match.group("label").strip().lower()
        operation = "encode" if label.endswith(" encode") else "decode"
        output_bytes = metric_match.group("output_bytes")
        measurements.append(
            Measurement(
                format=format_name,
                mode=mode,
                dataset=current_dataset,
                operation=operation,
                input_bytes=current_input_bytes,
                output_bytes=int(output_bytes) if output_bytes is not None else None,
                milliseconds=float(metric_match.group("milliseconds")),
                reported_mib_s=float(metric_match.group("reported_mib")),
                run=run,
            )
        )

    return measurements


def expected_measurements(format_name: str, mode: str) -> set[tuple[str, str]]:
    datasets = TYPED_DATASETS if mode == "typed" else frozenset(DATASETS)
    operations = ("decode",) if format_name == "json" else OPERATIONS
    return {(dataset, operation) for dataset in datasets for operation in operations}


def validate_measurements(measurements: Sequence[Measurement], format_name: str, mode: str) -> None:
    actual = [(item.dataset, item.operation) for item in measurements]
    if len(actual) != len(set(actual)):
        raise RuntimeError(f"{format_name}/{mode}: duplicate metric lines in benchmark output")

    expected = expected_measurements(format_name, mode)
    actual_set = set(actual)
    missing = sorted(expected - actual_set)
    unexpected = sorted(actual_set - expected)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(f"{dataset}/{operation}" for dataset, operation in missing))
        if unexpected:
            details.append("unexpected " + ", ".join(f"{dataset}/{operation}" for dataset, operation in unexpected))
        raise RuntimeError(f"{format_name}/{mode}: " + "; ".join(details))


def run_process(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            list(command),
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start {' '.join(command)}: {exc}") from exc

    # std.debug.print writes to stderr, while build diagnostics may use either
    # stream. Parsing the concatenation keeps the driver independent of that
    # implementation detail.
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        tail = "\n".join(output.splitlines()[-40:])
        raise RuntimeError(
            f"benchmark command failed with exit code {result.returncode}:\n"
            f"  {' '.join(command)}\n{tail}"
        )
    return output


def run_benchmarks(
    formats: Sequence[str],
    modes: Sequence[str],
    runs: int,
    command_options: argparse.Namespace,
    output_dir: Path,
) -> tuple[list[Measurement], dict[tuple[str, str], list[str]]]:
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    all_measurements: list[Measurement] = []
    commands: dict[tuple[str, str], list[str]] = {}

    for format_name in formats:
        for mode in modes:
            command = build_command(
                format_name,
                mode,
                command_options.zig,
                command_options.optimize,
                no_build=command_options.no_build,
                binary_dir=command_options.binary_dir,
            )
            commands[(format_name, mode)] = command
            for run in range(1, runs + 1):
                print(f"[{format_name}/{mode}] run {run}/{runs}: {' '.join(command)}", flush=True)
                output = run_process(command)
                raw_path = raw_dir / f"{format_name}-{mode}-{run:02d}.txt"
                raw_path.write_text(output, encoding="utf-8")
                parsed = parse_output(output, format_name, mode, run)
                validate_measurements(parsed, format_name, mode)
                all_measurements.extend(parsed)
                print(f"  parsed {len(parsed)} measurements -> {raw_path}", flush=True)

    return all_measurements, commands


def aggregate(measurements: Iterable[Measurement]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str, str], list[Measurement]] = {}
    for measurement in measurements:
        key = (measurement.format, measurement.mode, measurement.dataset, measurement.operation)
        groups.setdefault(key, []).append(measurement)

    rows: list[dict[str, object]] = []
    for key in sorted(groups):
        format_name, mode, dataset, operation = key
        samples = groups[key]
        durations = [sample.milliseconds for sample in samples]
        measured_bytes = {sample.measured_bytes for sample in samples}
        if len(measured_bytes) != 1:
            raise RuntimeError(f"{format_name}/{mode}/{dataset}/{operation}: payload size changed between runs")
        median_ms = statistics.median(durations)
        measured = measured_bytes.pop()
        throughput_mib_s = measured / (median_ms / 1000.0) / (1024.0 * 1024.0)
        throughput_gb_s = measured / (median_ms / 1000.0) / 1_000_000_000.0
        rows.append(
            {
                "format": format_name,
                "mode": mode,
                "dataset": dataset,
                "operation": operation,
                "input_bytes": samples[0].input_bytes,
                "output_bytes": samples[0].output_bytes,
                "measured_bytes": measured,
                "runs": len(samples),
                "median_ms": median_ms,
                "min_ms": min(durations),
                "max_ms": max(durations),
                "stdev_ms": statistics.stdev(durations) if len(durations) > 1 else 0.0,
                "throughput_mib_s": throughput_mib_s,
                "throughput_gb_s": throughput_gb_s,
            }
        )
    return rows


def write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    fields = [
        "format",
        "mode",
        "dataset",
        "operation",
        "input_bytes",
        "output_bytes",
        "measured_bytes",
        "runs",
        "median_ms",
        "min_ms",
        "max_ms",
        "stdev_ms",
        "throughput_mib_s",
        "throughput_gb_s",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: Sequence[dict[str, object]], runs: int) -> None:
    by_key = {(str(row["format"]), str(row["mode"]), str(row["dataset"]), str(row["operation"])): row for row in rows}
    series_order = [(format_name, mode) for format_name in FORMATS for mode in MODES]
    datasets = [dataset for dataset in DATASETS if any(key[2] == dataset for key in by_key)]

    lines = [
        "# serde.zig benchmark results",
        "",
        f"Median of {runs} process run(s). Throughput is calculated from the median `ms/op`.",
        "JSON decode uses input bytes; MessagePack encode uses encoded output bytes.",
        "",
    ]
    for operation in OPERATIONS:
        available = [
            (format_name, mode)
            for format_name, mode in series_order
            if any((format_name, mode, dataset, operation) in by_key for dataset in datasets)
        ]
        if not available:
            continue
        lines.extend(
            [
                f"## {operation.title()} throughput",
                "",
                "| Dataset | "
                + " | ".join(f"{FORMAT_LABELS[format_name]} / {mode}" for format_name, mode in available)
                + " |",
                "| --- | " + " | ".join("---:" for _ in available) + " |",
            ]
        )
        for dataset in datasets:
            values: list[str] = []
            for format_name, mode in available:
                row = by_key.get((format_name, mode, dataset, operation))
                values.append("-" if row is None else f"{float(row['throughput_gb_s']):.3f} GB/s")
            lines.append(f"| {dataset.removesuffix('.json')} | " + " | ".join(values) + " |")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_summary(path: Path) -> tuple[list[dict[str, object]], int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("summary")
    if not isinstance(rows, list):
        raise RuntimeError(f"{path} does not contain a summary array")
    runs = int(payload.get("metadata", {}).get("runs", 1))
    return rows, runs


class MissingDependency(RuntimeError):
    """Raised when a rendering dependency (plotly) is not installed."""


CHART_COLORS = {
    ("json", "generic"): "#ff1025",
    ("json", "typed"): "#111111",
    ("msgpack", "generic"): "#6d6900",
    ("msgpack", "typed"): "#1639ef",
}

CHART_LABELS = {
    ("json", "generic"): "JSON / generic",
    ("json", "typed"): "JSON / typed",
    ("msgpack", "generic"): "MessagePack / generic",
    ("msgpack", "typed"): "MessagePack / typed",
}


def write_html_page(path: Path, rows: Sequence[dict[str, object]], runs: int) -> None:
    """Write a self-contained interactive page (plotly) with one chart per operation."""
    try:
        import plotly.graph_objects as go
        import plotly.io as pio
        from plotly.offline import get_plotlyjs
    except ImportError as exc:
        raise MissingDependency(
            "the results page requires plotly; run with `uv run bench.py` "
            "(uv installs it from the PEP 723 header) or `pip install plotly`"
        ) from exc

    by_key = {
        (str(row["format"]), str(row["mode"]), str(row["dataset"]), str(row["operation"])): row
        for row in rows
    }
    datasets = [dataset for dataset in DATASETS if any(key[2] == dataset for key in by_key)]
    short_names = [dataset.removesuffix(".json") for dataset in datasets]

    sections: list[str] = []
    for operation in OPERATIONS:
        available = [
            series
            for series in CHART_LABELS
            if any((*series, dataset, operation) in by_key for dataset in datasets)
        ]
        if not available:
            continue

        fig = go.Figure()
        for series in available:
            throughput: list[float | None] = []
            hover: list[list[float] | None] = []
            for dataset in datasets:
                row = by_key.get((*series, dataset, operation))
                if row is None:
                    throughput.append(None)
                    hover.append(None)
                else:
                    throughput.append(float(row["throughput_gb_s"]))
                    hover.append([float(row["median_ms"]), float(row["throughput_mib_s"])])
            fig.add_trace(
                go.Bar(
                    name=CHART_LABELS[series],
                    x=short_names,
                    y=throughput,
                    marker_color=CHART_COLORS[series],
                    customdata=hover,
                    hovertemplate=(
                        "%{x}<br><b>%{y:.3f} GB/s</b>"
                        "<br>median %{customdata[0]:.2f} ms"
                        "<extra>%{fullData.name}</extra>"
                    ),
                )
            )

        fig.update_layout(
            barmode="group",
            title=dict(text=f"{operation.title()} throughput", x=0.0),
            xaxis=dict(title="corpus file", tickangle=-32),
            yaxis=dict(title="throughput (GB/s)", rangemode="tozero"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            template="plotly_white",
            height=560,
            margin=dict(l=72, r=24, t=80, b=110),
        )
        sections.append(
            "<section>\n"
            f"<h2>{operation.title()} throughput</h2>\n"
            f"{pio.to_html(fig, full_html=False, include_plotlyjs=False)}\n"
            "</section>"
        )

    if not sections:
        raise RuntimeError("no measurements to chart for the selected formats/modes")

    body = "\n".join(sections)
    plotly_js = get_plotlyjs().replace("</script>", "<\\/script>")
    note = (
        f"Median of {runs} process run(s); the timed path excludes file loading "
        "and cleanup. JSON decode throughput uses the JSON input size; "
        "MessagePack encode uses the encoded output size."
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>serde.zig benchmark</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px 32px 48px; color: #222; }}
  header h1 {{ margin: 0 0 6px; font-size: 24px; }}
  header p  {{ margin: 0 0 8px; color: #555; font-size: 14px; max-width: 72em; }}
  h2 {{ font-size: 18px; border-bottom: 1px solid #e5e5e5; padding-bottom: 6px; }}
  section {{ margin-top: 30px; }}
  footer {{ margin-top: 48px; color: #999; font-size: 12px; }}
</style>
<script>{plotly_js}</script>
</head>
<body>
<header>
  <h1>serde.zig real-world benchmark</h1>
  <p>{note}</p>
</header>
{body}
<footer>Generated by bench.py; raw logs live in raw/ and measurements.json next to this page.</footer>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--format",
        dest="format_name",
        choices=("json", "msgpack", "all"),
        default="all",
        help="benchmark one format or both (default: all)",
    )
    result.add_argument(
        "--mode",
        choices=("generic", "typed", "all"),
        default="all",
        help="benchmark one representation or both (default: all)",
    )
    result.add_argument(
        "--runs",
        type=positive_int,
        default=3,
        help="independent process runs per format/mode (default: 3)",
    )
    result.add_argument(
        "--output",
        dest="exports",
        action="append",
        choices=("csv", "md"),
        default=None,
        help="also write summary.csv / summary.md next to the page (repeatable)",
    )
    result.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"directory for index.html, measurements.json and raw/ logs (default: {DEFAULT_OUTPUT})",
    )
    result.add_argument("--zig", default=os.environ.get("ZIG", "zig"), help="Zig executable (default: $ZIG or zig)")
    result.add_argument(
        "--optimize",
        default="ReleaseFast",
        help="optimization mode forwarded as -Doptimize (default: ReleaseFast; build.zig otherwise defaults to Debug)",
    )
    result.add_argument(
        "--no-build",
        action="store_true",
        help="run existing zig-out binaries instead of invoking zig build",
    )
    result.add_argument(
        "--binary-dir",
        type=Path,
        default=ROOT / "zig-out" / "bin",
        help="directory containing json-bench/msgpack-bench for --no-build",
    )
    result.add_argument(
        "--plot-only",
        action="store_true",
        help="rebuild index.html and any --output exports from the saved measurements.json without running Zig",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    formats = selected_values(args.format_name, FORMATS, "format")
    modes = selected_values(args.mode, MODES, "mode")

    if args.plot_only:
        summary_path = output_dir / "measurements.json"
        if not summary_path.is_file():
            raise SystemExit(f"--plot-only requires {summary_path}")
        summary, runs = load_summary(summary_path)
    else:
        measurements, commands = run_benchmarks(formats, modes, args.runs, args, output_dir)
        summary = aggregate(measurements)
        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(ROOT),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "runs": args.runs,
            "commands": {f"{format_name}/{mode}": command for (format_name, mode), command in commands.items()},
        }
        payload = {
            "metadata": metadata,
            "measurements": [
                asdict(measurement) | {"measured_bytes": measurement.measured_bytes}
                for measurement in measurements
            ],
            "summary": summary,
        }
        (output_dir / "measurements.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        runs = args.runs

    # Filtering here also makes --plot-only useful with --format/--mode.
    summary = [
        row
        for row in summary
        if str(row["format"]) in formats and str(row["mode"]) in modes
    ]

    written: list[Path] = []
    for export in args.exports or ():
        if export == "csv":
            path = output_dir / "summary.csv"
            write_csv(path, summary)
        else:
            path = output_dir / "summary.md"
            write_markdown(path, summary, runs)
        written.append(path)

    page = output_dir / "index.html"
    page_ready = False
    try:
        write_html_page(page, summary, runs)
    except MissingDependency as exc:
        if args.exports:
            print(f"bench.py: warning: {exc}; exports written without the page", file=sys.stderr)
        else:
            raise
    else:
        page_ready = True
        written.append(page)

    for path in written:
        print(f"wrote {path}")
    if page_ready:
        webbrowser.open(page.resolve().as_uri())
        print(f"opened {page} in your browser")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"bench.py: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
