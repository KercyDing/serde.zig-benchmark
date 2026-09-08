"""Run the real-world serde.zig benchmark and open an interactive results page.

The Zig programs already perform warmup and repeat each fixture according to
its size.  This driver runs every format/representation/implementation
combination in separate processes, keeps the raw text for auditability,
aggregates process runs by median, and renders one web page
(``results/index.html``) with Highcharts column charts, then
opens it in your browser. No Python chart library is needed; the page loads
Highcharts from a CDN (first open requires network).

Typical use::

    uv run bench.py              # run all, write html + csv + md, open page
    uv run bench.py --output csv # only summary.csv (or --output md)

Raw per-process output and ``measurements.json`` are kept under the result
directory (``results``); the page and the optional CSV/Markdown
exports are derived from them.
"""

import argparse
import csv
import json
import os
import platform
import re
import statistics
import sys
import webbrowser
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

from engine import positive_int, run_repetitions

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "results"


def selected_values(value: str, choices: Sequence[str], name: str) -> tuple[str, ...]:
    if value == "all":
        return tuple(choices)
    if value not in choices:
        valid = ", ".join((*choices, "all"))
        raise ValueError(f"invalid {name} {value!r}; choose from {valid}")
    return (value,)


def serde_version() -> str:
    """Read the pinned serde.zig version from its package hash."""
    root_zon = (ROOT / "build.zig.zon").read_text(encoding="utf-8")
    if match := re.search(r'\.serde\s*=\s*\.\{.*?\.hash\s*=\s*"serde-([0-9]+(?:\.[0-9]+)*)-', root_zon, re.DOTALL):
        return f"v{match.group(1)}"
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
OPERATIONS = ("roundtrip", "decode", "encode")
# User-facing tasks. Each library participates with its native path (typed
# structs or generic values) and contributes one total per task.
TASK_SPECS = (
    ("Encode known data", "typed", "encode"),
    ("Decode known data", "typed", "decode"),
    ("Load arbitrary data", "generic", "decode"),
    ("Transform data", "generic", "roundtrip"),
)
FORMAT_LABELS = {"json": "JSON", "msgpack": "MessagePack"}
IMPLEMENTATIONS = ("serde", "std.json")
MSGPACK_IMPLEMENTATIONS = ("serde", "msgpack.zig", "zig-msgpack")
# msgpack.zig (lalinsky) is typed-only; zig-msgpack is generic (Payload DOM) only.
MSGPACK_SUPPORTED_MODES = {"serde": MODES, "msgpack.zig": ("typed",), "zig-msgpack": ("generic",)}
# Per-implementation datasets that cannot be benchmarked (msgpack.zig cannot
# decode fixed-array geometry types in canada.json).
IMPL_EXCLUDED_DATASETS = {"msgpack.zig": {"canada.json"}}


def implementations_for_format(format_name: str) -> tuple[str, ...]:
    return IMPLEMENTATIONS if format_name == "json" else MSGPACK_IMPLEMENTATIONS


def supported_modes(format_name: str, implementation: str) -> tuple[str, ...]:
    if format_name == "json":
        return MODES
    return MSGPACK_SUPPORTED_MODES[implementation]


def implementation_argument(implementation: str) -> str:
    return {
        "serde": "serde",
        "std.json": "std",
        "msgpack.zig": "lalinsky",
        "zig-msgpack": "zig_msgpack",
    }[implementation]


def implementation_directory(implementation: str) -> str:
    return {
        "serde": "serde.zig",
        "std.json": "std",
        "msgpack.zig": "msgpack.zig",
        "zig-msgpack": "zig_msgpack",
    }[implementation]

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
    implementation: str
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
        if self.output_bytes is not None:
            return self.output_bytes
        return self.input_bytes


class SummaryRow(TypedDict):
    format: str
    implementation: str
    mode: str
    dataset: str
    operation: str
    input_bytes: int
    output_bytes: int | None
    measured_bytes: int
    runs: int
    median_ms: float
    min_ms: float
    max_ms: float
    stdev_ms: float
    throughput_mib_s: float
    throughput_gb_s: float





def build_command(
    format_name: str,
    implementation: str,
    mode: str,
    zig: str,
    optimize: str | None,
) -> list[str]:
    if format_name == "json":
        step = "bench-json"
        build_args = [f"-Dmode={mode}", f"-Dimplementation={implementation_argument(implementation)}"]
    else:
        step = {
            "serde": "bench-msgpack-serde",
            "msgpack.zig": "bench-msgpack-msgpack-zig",
            "zig-msgpack": "bench-msgpack-zig-msgpack",
        }[implementation]
        build_args = [f"-Dmode={mode}"]
    command = [zig, "build", step, *build_args]
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
        if label.startswith("std.json "):
            implementation = "std.json"
        elif label.startswith("msgpack.zig "):
            implementation = "msgpack.zig"
        elif label.startswith("zig-msgpack "):
            implementation = "zig-msgpack"
        else:
            implementation = "serde"
        if label.endswith(" encode"):
            operation = "encode"
        elif label.endswith(" roundtrip"):
            operation = "roundtrip"
        else:
            operation = "decode"
        output_bytes = metric_match.group("output_bytes")
        measurements.append(
            Measurement(
                format=format_name,
                implementation=implementation,
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


def expected_measurements(format_name: str, implementation: str, mode: str) -> set[tuple[str, str, str]]:
    datasets = TYPED_DATASETS if mode == "typed" else frozenset(DATASETS)
    datasets = frozenset(dataset for dataset in datasets if dataset not in IMPL_EXCLUDED_DATASETS.get(implementation, ()))
    return {(implementation, dataset, operation) for dataset in datasets for operation in OPERATIONS}


def validate_measurements(measurements: Sequence[Measurement], format_name: str, implementation: str, mode: str) -> None:
    actual = [(item.implementation, item.dataset, item.operation) for item in measurements]
    if len(actual) != len(set(actual)):
        raise RuntimeError(f"{format_name}/{mode}: duplicate metric lines in benchmark output")

    expected = expected_measurements(format_name, implementation, mode)
    actual_set = set(actual)
    missing = sorted(expected - actual_set)
    unexpected = sorted(actual_set - expected)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(f"{implementation}/{dataset}/{operation}" for implementation, dataset, operation in missing))
        if unexpected:
            details.append("unexpected " + ", ".join(f"{implementation}/{dataset}/{operation}" for implementation, dataset, operation in unexpected))
        raise RuntimeError(f"{format_name}/{mode}: " + "; ".join(details))


def run_benchmarks(
    formats: Sequence[str],
    runs: int,
    zig: str,
    optimize: str,
    output_dir: Path,
) -> tuple[list[Measurement], dict[tuple[str, str, str], list[str]]]:
    all_measurements: list[Measurement] = []
    commands: dict[tuple[str, str, str], list[str]] = {}
    for format_name in formats:
        for implementation in implementations_for_format(format_name):
            raw_dir = output_dir / format_name / implementation_directory(implementation)
            for mode in supported_modes(format_name, implementation):
                command = build_command(format_name, implementation, mode, zig, optimize)
                commands[(format_name, implementation, mode)] = command
                collected = run_repetitions(
                    label=f"{format_name}/{implementation}/{mode}",
                    command=command,
                    raw_dir=raw_dir,
                    stem=mode,
                    runs=runs,
                    parse=lambda out, run, f=format_name, m=mode: parse_output(out, f, m, run),
                    validate=lambda meas, f=format_name, i=implementation, m=mode: validate_measurements(meas, f, i, m),
                )
                all_measurements.extend(cast(list[Measurement], collected))
    return all_measurements, commands


def aggregate(measurements: Iterable[Measurement]) -> list[SummaryRow]:
    groups: dict[tuple[str, str, str, str, str], list[Measurement]] = {}
    for measurement in measurements:
        key = (measurement.format, measurement.implementation, measurement.mode, measurement.dataset, measurement.operation)
        groups.setdefault(key, []).append(measurement)

    rows: list[SummaryRow] = []
    for key in sorted(groups):
        format_name, implementation, mode, dataset, operation = key
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
                "implementation": implementation,
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


def write_csv(path: Path, rows: Sequence[SummaryRow]) -> None:
    fields = [
        "format",
        "implementation",
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


def write_markdown(path: Path, rows: Sequence[SummaryRow], runs: int) -> None:
    by_key = {
        (str(row["format"]), str(row["implementation"]), str(row["mode"]), str(row["dataset"]), str(row["operation"])): row
        for row in rows
    }

    lines = [
        "# serde.zig benchmark results",
        "",
        f"serde.zig {serde_version()} · median of {runs} process run(s). Throughput is calculated from the median `ms/op`.",
        "Decode throughput uses input bytes; encode throughput uses encoded output bytes.",
        "",
    ]
    for format_name in FORMATS:
        for task, mode, operation in TASK_SPECS:
            implementations = [
                implementation
                for implementation in implementations_for_format(format_name)
                if any(
                    (format_name, implementation, mode, dataset, operation) in by_key
                    for dataset in DATASETS
                )
            ]
            if not implementations:
                continue
            task_datasets = [
                dataset
                for dataset in DATASETS
                if any((format_name, implementation, mode, dataset, operation) in by_key for implementation in implementations)
            ]
            lines.extend(
                [
                    f"## {FORMAT_LABELS[format_name]} · {task}",
                    "",
                    "| Dataset | " + " | ".join(implementations) + " |",
                    "| --- | " + " | ".join("---:" for _ in implementations) + " |",
                ]
            )
            for dataset in task_datasets:
                values: list[str] = []
                for implementation in implementations:
                    row = by_key.get((format_name, implementation, mode, dataset, operation))
                    values.append("-" if row is None else f"{float(row['throughput_gb_s']):.3f} GB/s")
                lines.append(f"| {dataset.removesuffix('.json')} | " + " | ".join(values) + " |")
            lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_summary(path: Path) -> tuple[list[SummaryRow], int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("summary")
    if not isinstance(rows, list):
        raise TypeError(f"{path} does not contain a summary array")
    runs = int(payload.get("metadata", {}).get("runs", 1))
    return cast(list[SummaryRow], rows), runs


HIGHCHARTS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/highcharts/8.2.0/"

def write_html_page(path: Path, rows: Sequence[SummaryRow], runs: int) -> None:
    """Write an interactive Highcharts page (library from CDN, like yyjson).

    Layout: encode and decode charts for each format, with implementation and
    representation shown as separate series. No Python chart library.
    """
    by_key = {
        (str(row["format"]), str(row["implementation"]), str(row["mode"]), str(row["dataset"]), str(row["operation"])): row
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
        key_implementation: str,
        key_mode: str,
        operation: str,
        datasets: list[str],
    ) -> list[float | None]:
        return [
            None
            if (key_format, key_implementation, key_mode, dataset, operation) not in by_key
            else float(by_key[(key_format, key_implementation, key_mode, dataset, operation)]["throughput_gb_s"])
            for dataset in datasets
        ]

    def collect_series(
        series_specs: list[tuple[str, str, str, str, str, list[str]]],
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for name, key_format, key_implementation, key_mode, operation, datasets in series_specs:
            result.append(
                {
                    "name": name,
                    "data": series_points(key_format, key_implementation, key_mode, operation, datasets),
                }
            )
        return result

    def card(heading: str, plot: str, description: str = "") -> str:
        detail = "" if not description else f'<p class="chart-note">{description}</p>\n'
        return "<section class=\"plot\">\n" f"<h2>{heading}</h2>\n{detail}{plot}\n" "</section>"

    sections: list[str] = []

    # Cards grouped by user task. typed/generic stay an implementation detail
    # (they select which libraries can enter a task), not a first-level label.
    for format_name in FORMATS:
        label = FORMAT_LABELS[format_name]
        implementations = implementations_for_format(format_name)
        for task, mode, operation in TASK_SPECS:
            group = [
                dataset
                for dataset in DATASETS
                if any(
                    (format_name, implementation, mode, dataset, operation) in by_key
                    for implementation in implementations
                )
            ]
            if not group:
                continue
            series = collect_series(
                [
                    (
                        implementation,
                        format_name,
                        implementation,
                        mode,
                        operation,
                        group,
                    )
                    for implementation in implementations
                    if any(
                        (format_name, implementation, mode, dataset, operation) in by_key
                        for dataset in group
                    )
                ]
            )
            if series:
                sections.append(
                    card(
                        f"{label} · {task}",
                        chart_html(group, series, 430, f"{format_name}-{mode}-{operation}"),
                    )
                )

    if not sections:
        raise RuntimeError("no measurements to chart for the selected formats/modes")

    body = "\n".join(sections)
    note = (
        f"Median of {runs} process run(s) · serde.zig {serde_version()} and std.json · "
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
  .chart-note {{ margin: -4px 0 12px; color: #4a5568; font-size: 14px; }}
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
<footer>Generated by bench.py; raw logs live in json/ and msgpack/, with measurements.json next to this page.</footer>
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
        "--runs",
        type=positive_int,
        default=10,
        help="independent process runs per format (default: 10)",
    )
    result.add_argument(
        "--thread",
        type=positive_int,
        default=None,
        help="also run a thread-scaling sweep up to N workers (default: none)",
    )
    result.add_argument(
        "--output",
        dest="exports",
        action="append",
        choices=("csv", "md"),
        default=None,
        help="write only the chosen summary file(s), without the page (repeatable)",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    output_dir = DEFAULT_OUTPUT.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    formats = selected_values(args.format_name, FORMATS, "format")
    zig = os.environ.get("ZIG", "zig")
    optimize = "ReleaseFast"

    measurements, commands = run_benchmarks(formats, args.runs, zig, optimize, output_dir)
    summary = aggregate(measurements)
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(ROOT),
        "serde": serde_version(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "runs": args.runs,
        "commands": {f"{format_name}/{implementation}/{mode}": command for (format_name, implementation, mode), command in commands.items()},
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

    written: list[Path] = []
    if args.exports:
        want_csv = "csv" in args.exports
        want_md = "md" in args.exports
        want_page = False
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
    page: Path | None = None
    if want_page:
        page = output_dir / "index.html"
        write_html_page(page, summary, runs)
        written.append(page)

    open_pages = [page] if page else []
    if args.thread is not None:
        from scaling import run_scaling

        extra, scaling_page = run_scaling(
            formats=formats,
            runs=args.runs,
            max_threads=args.thread,
            output_dir=output_dir,
            exports=args.exports,
        )
        written.extend(extra)
        if scaling_page is not None:
            open_pages.append(scaling_page)

    for path in written:
        print(f"wrote {path}")
    for page_path in open_pages:
        webbrowser.open(page_path.resolve().as_uri())
        print(f"opened {page_path} in your browser")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as exc:
        print(f"bench.py: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
