#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Run the real-world serde.zig benchmark and open an interactive results page.

The Zig programs already perform warmup and repeat each fixture according to
its size.  This driver runs each selected target in separate processes, keeps
the raw text for auditability, aggregates process runs by median, and renders
one web page (``bench-results/index.html``) with Highcharts column charts,
then opens it in your browser. No Python chart library is needed; the page
loads Highcharts from a CDN (first open requires network), like the yyjson
benchmark reports.

Typical use::

    uv run bench.py                      # index.html + csv + md, opens the page
    uv run bench.py --no-plot            # summary.csv + summary.md, no page
    uv run bench.py --output csv         # only summary.csv
    uv run bench.py --runs 20 --mode generic
    python3 bench.py --plot-only         # rebuild outputs from saved results

Raw per-process output and ``measurements.json`` are always kept under the
result directory (``bench-results`` by default) for auditability; the page and
the optional CSV/Markdown exports are derived from them. Decode throughput
uses input bytes; encode throughput uses encoded output bytes, matching the
benchmark implementation.
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


def serde_version() -> str:
    """Read the pinned serde.zig tag from the build.zig.zon dependency URL."""
    root_zon = (ROOT / "build.zig.zon").read_text(encoding="utf-8")
    if match := re.search(r"tags/(v[0-9]+(?:\.[0-9]+)*)", root_zon):
        return match.group(1)
    if match := re.search(r"ref=([A-Za-z0-9_.-]+)", root_zon):
        return match.group(1)
    return "unknown"

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
    return {(dataset, operation) for dataset in datasets for operation in OPERATIONS}


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
        f"serde.zig {serde_version()} · median of {runs} process run(s). Throughput is calculated from the median `ms/op`.",
        "Decode throughput uses input bytes; encode throughput uses encoded output bytes.",
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


HIGHCHARTS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/highcharts/8.2.0/"

FORMAT_SERIES_ORDER = (
    ("json", "generic"),
    ("json", "typed"),
    ("msgpack", "generic"),
    ("msgpack", "typed"),
)

def write_html_page(path: Path, rows: Sequence[dict[str, object]], runs: int) -> None:
    """Write an interactive Highcharts page (library from CDN, like yyjson).

    Layout: JSON-vs-MessagePack encode and decode comparisons over the shared
    typed corpus on top, followed by encode and decode charts for each format.
    No Python chart library.
    """
    by_key = {
        (str(row["format"]), str(row["mode"]), str(row["dataset"]), str(row["operation"])): row
        for row in rows
    }
    chart_counter = 0

    def chart_html(
        datasets: list[str],
        series: list[dict[str, object]],
        height: int,
        filename: str,
    ) -> str:
        nonlocal chart_counter
        chart_counter += 1
        container_id = f"chart-{chart_counter}"
        categories = [dataset.removesuffix(".json") for dataset in datasets]
        options = {
            "chart": {
                "type": "column",
                "height": height,
                "backgroundColor": "transparent",
                "animation": True,
            },
            "title": {"text": None},
            "credits": {"enabled": False},
            "exporting": {"filename": filename},
            "xAxis": {
                "categories": categories,
                "labels": {"rotation": -35, "style": {"fontSize": "11px"}},
            },
            "yAxis": {"title": {"text": "throughput (GB/s)"}, "min": 0},
            "tooltip": {"shared": True, "valueDecimals": 3, "valueSuffix": " GB/s"},
            "legend": {"layout": "horizontal", "align": "center", "verticalAlign": "top"},
            "plotOptions": {"column": {"borderRadius": 3, "pointPadding": 0.06, "groupPadding": 0.2}},
            "series": series,
        }
        return (
            f'<div id="{container_id}" class="hc-container"></div>\n'
            f"<script>Highcharts.chart({json.dumps(container_id)}, {json.dumps(options)});</script>\n"
        )

    def series_points(
        key_format: str,
        key_mode: str,
        operation: str,
        datasets: list[str],
    ) -> list[float | None]:
        return [
            None
            if (key_format, key_mode, dataset, operation) not in by_key
            else float(by_key[(key_format, key_mode, dataset, operation)]["throughput_gb_s"])
            for dataset in datasets
        ]

    def collect_series(
        series_specs: list[tuple[str, str, str, str, list[str]]],
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for name, key_format, key_mode, operation, datasets in series_specs:
            result.append(
                {
                    "name": name,
                    "data": series_points(key_format, key_mode, operation, datasets),
                }
            )
        return result

    def card(heading: str, plot: str) -> str:
        return "<section class=\"plot\">\n" f"<h2>{heading}</h2>\n{plot}\n" "</section>"

    # Top comparisons use only datasets with typed models in both formats.
    compare_datasets = [
        dataset
        for dataset in DATASETS
        if any((format_name, "typed", dataset, "decode") in by_key for format_name in FORMATS)
    ]

    sections: list[str] = []
    for operation in OPERATIONS:
        compare_series = collect_series(
            [
                (
                    f"{FORMAT_LABELS[format_name]} / {mode}",
                    format_name,
                    mode,
                    operation,
                    compare_datasets,
                )
                for format_name, mode in FORMAT_SERIES_ORDER
                if any((format_name, mode, dataset, operation) in by_key for dataset in compare_datasets)
            ]
        )
        if compare_series:
            sections.append(card(
                f"JSON vs MessagePack — shared typed datasets ({operation})",
                chart_html(compare_datasets, compare_series, 460, f"json-vs-msgpack-{operation}"),
            ))

    # One encode and one decode card for each format.
    for format_name in FORMATS:
        label = FORMAT_LABELS[format_name]
        available_datasets = [
            dataset
            for dataset in DATASETS
            if any(
                (format_name, mode, dataset, operation) in by_key
                for mode in MODES
                for operation in OPERATIONS
            )
        ]
        group_datasets = [
            dataset
            for dataset in available_datasets
            if any(
                (format_name, "typed", dataset, operation) in by_key
                for operation in OPERATIONS
            )
        ]
        group_datasets.extend(
            dataset
            for dataset in available_datasets
            if dataset not in group_datasets
        )
        if not group_datasets:
            continue
        for operation in OPERATIONS:
            series = collect_series(
                [
                    (
                        mode,
                        format_name,
                        mode,
                        operation,
                        group_datasets,
                    )
                    for mode in MODES
                    if any((format_name, mode, dataset, operation) in by_key for dataset in group_datasets)
                ]
            )
            if series:
                sections.append(
                    card(
                        f"{label} — {operation}",
                        chart_html(group_datasets, series, 430, f"{format_name}-{operation}"),
                    )
                )

    if not sections:
        raise RuntimeError("no measurements to chart for the selected formats/modes")

    body = "\n".join(sections)
    note = (
        f"Median of {runs} process run(s) · serde.zig {serde_version()} · "
        "timed path excludes file loading and cleanup."
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>serde.zig benchmark</title>
<script src="{HIGHCHARTS_CDN}highcharts.js"></script>
<script src="{HIGHCHARTS_CDN}modules/exporting.js"></script>
<script src="{HIGHCHARTS_CDN}modules/offline-exporting.js"></script>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px 32px 48px; background: #f3f6f9; color: #1f2733; }}
  header {{ max-width: 1000px; margin: 0 auto 22px; }}
  header h1 {{ margin: 0 0 6px; font-size: 24px; color: #141a23; }}
  header p  {{ margin: 0; color: #4a5568; font-size: 14px; }}
  h2 {{ font-size: 16px; margin: 0 0 12px; color: #141a23; }}
  section.plot {{ max-width: 1000px; margin: 0 auto 28px; background: #ffffff; border-radius: 12px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(16, 24, 40, 0.08); }}
  .hc-container {{ width: 100%; }}
  footer {{ max-width: 1000px; margin: 4px auto 0; color: #718096; font-size: 12px; }}
</style>
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
        default=10,
        help="independent process runs per format/mode (default: 10)",
    )
    result.add_argument(
        "--output",
        dest="exports",
        action="append",
        choices=("csv", "md"),
        default=None,
        help="write only the chosen summary file(s), without the page (repeatable)",
    )
    result.add_argument(
        "--no-plot",
        action="store_true",
        help="write summary.csv and summary.md without the page",
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
        help="rebuild the page or exports from the saved measurements.json without running Zig",
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
            "serde": serde_version(),
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
    if args.exports:
        want_csv = "csv" in args.exports
        want_md = "md" in args.exports
        want_page = False
    elif args.no_plot:
        want_csv, want_md, want_page = True, True, False
    else:
        want_csv, want_md, want_page = True, True, True

    if want_csv:
        path = output_dir / "summary.csv"
        write_csv(path, summary)
        written.append(path)
    if want_md:
        path = output_dir / "summary.md"
        write_markdown(path, summary, runs)
        written.append(path)
    if want_page:
        page = output_dir / "index.html"
        write_html_page(page, summary, runs)
        written.append(page)

    for path in written:
        print(f"wrote {path}")
    if want_page:
        webbrowser.open(page.resolve().as_uri())
        print(f"opened {page} in your browser")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"bench.py: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
