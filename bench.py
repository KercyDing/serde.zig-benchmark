"""Run zig-serde-bench and open the interactive result pages.

Two wire formats (JSON and MessagePack) are measured across four user tasks,
each run by the library implementations that support it:

* Encode known data      (known-encode)
* Decode known data      (known-decode)
* Load arbitrary data    (arbitrary-decode)
* Transform data         (transform)

The Zig programs already perform warmup and repeat each fixture according to
its size and print one task-tagged metric line per dataset. This driver runs
every format/implementation combination in separate processes, keeps the raw
text for auditability, aggregates process runs by median, and renders one web
page per format (``results/json/index.html`` and
``results/msgpack/index.html``) with Highcharts column charts — for each task
two side-by-side charts: throughput in GB/s and rate in ops/s. Then it opens
the generated pages in your browser.
No Python chart library is needed; the page loads Highcharts from a CDN (first
open requires network).

Typical use::

    uv run bench.py               # run all, write html + csv + md, open page
    uv run bench.py --no-build    # regenerate reports from existing results

Raw per-process output and ``measurements.json`` are kept under the result
format directory; the HTML, CSV, and Markdown reports are derived from them.
"""

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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, TypeVar, cast

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "results"

T = TypeVar("T")


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def run_process(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            list(command),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start {' '.join(command)}: {exc}") from exc

    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        tail = "\n".join(output.splitlines()[-40:])
        raise RuntimeError(
            f"benchmark command failed with exit code {result.returncode}:\n"
            f"  {' '.join(command)}\n{tail}"
        )
    return output


def run_repetitions(
    *,
    label: str,
    command: Sequence[str],
    raw_dir: Path,
    stem: str,
    runs: int,
    parse: Callable[[str, int], list[T]],
    validate: Callable[[list[T]], None],
) -> list[T]:
    """Run one command ``runs`` times, write each raw log, and collect samples."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    collected: list[T] = []
    for run in range(1, runs + 1):
        print(f"[{label}] run {run}/{runs}: {' '.join(command)}", flush=True)
        output = run_process(command)
        raw_path = raw_dir / f"{stem}-{run:02d}.txt"
        raw_path.write_text(output, encoding="utf-8")
        parsed = parse(output, run)
        validate(parsed)
        collected.extend(parsed)
        print(f"  parsed {len(parsed)} measurements -> {raw_path}", flush=True)
    return collected


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


ALL_DATASETS = (
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
KNOWN_DATASETS = frozenset(
    {"canada.json", "github_events.json", "poet.json", "twitter.json", "twitterescaped.json"}
)
FORMATS = ("json", "msgpack")
FORMAT_LABELS = {"json": "JSON", "msgpack": "MessagePack"}
JSON_IMPLEMENTATIONS = ("serde", "jsonz", "std.json")
MSGPACK_IMPLEMENTATIONS = ("serde", "msgpack.zig", "zig-msgpack")
# The four user tasks as (task label, metric token). Known-schema tasks
# (known-encode/known-decode) run only on KNOWN_DATASETS; the others run on
# every dataset.
TASKS = (
    ("Encode known data", "known-encode"),
    ("Decode known data", "known-decode"),
    ("Load arbitrary data", "arbitrary-decode"),
    ("Transform data", "transform"),
)
TASK_LABELS = {token: label for label, token in TASKS}
ALL_TOKENS = tuple(token for _, token in TASKS)
# Tokens each MessagePack implementation prints. JSON implementations print
# every token.
MSGPACK_TOKENS = {
    "serde": ALL_TOKENS,
    "msgpack.zig": ALL_TOKENS,
    "zig-msgpack": ALL_TOKENS,
}
JSON_TOKENS = {"serde": ALL_TOKENS, "jsonz": ALL_TOKENS, "std.json": ALL_TOKENS}


def implementations_for_format(format_name: str) -> tuple[str, ...]:
    return JSON_IMPLEMENTATIONS if format_name == "json" else MSGPACK_IMPLEMENTATIONS


def implementation_argument(implementation: str) -> str:
    """Build-step value for an implementation; the JSON step is the only one
    that takes an argument (msgpack.zig maps to the dependency name, unused
    here but kept for parity)."""
    return {
        "serde": "serde",
        "std.json": "std",
        "jsonz": "jsonz",
        "msgpack.zig": "lalinsky",
        "zig-msgpack": "zig_msgpack",
    }[implementation]


def implementation_directory(implementation: str) -> str:
    return {
        "serde": "serde.zig",
        "std.json": "std",
        "jsonz": "jsonz",
        "msgpack.zig": "msgpack.zig",
        "zig-msgpack": "zig_msgpack",
    }[implementation]


def supported_tokens(format_name: str, implementation: str) -> tuple[str, ...]:
    """Metric tokens that an implementation prints for a format."""
    if format_name == "json":
        return JSON_TOKENS[implementation]
    return MSGPACK_TOKENS[implementation]


def token_datasets(format_name: str, implementation: str, token: str) -> frozenset[str]:
    """Datasets where an (implementation, token) pair produces a metric.

    Known-schema tasks cover KNOWN_DATASETS; the remaining tasks cover every
    dataset.
    """
    if token in ("known-encode", "known-decode"):
        return KNOWN_DATASETS
    return frozenset(ALL_DATASETS)


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
    dataset: str
    task: str
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
    dataset: str
    task: str
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
    latency_us: float
    ops_per_s: float


def parse_label(label: str) -> tuple[str, str]:
    """Split a metric label into (implementation, token).

    Labels look like "serde known-encode" or "std.json transform"; anything
    without a known prefix is treated as serde and the trailing word is the
    token candidate.
    """
    for prefix, implementation in (
        ("std.json ", "std.json"),
        ("jsonz ", "jsonz"),
        ("msgpack.zig ", "msgpack.zig"),
        ("zig-msgpack ", "zig-msgpack"),
        ("serde ", "serde"),
    ):
        if label.startswith(prefix):
            return implementation, label[len(prefix) :].strip()
    return "serde", label.strip()


def parse_output(output: str, format_name: str, run: int) -> list[Measurement]:
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
        implementation, token = parse_label(label)
        task = TASK_LABELS.get(token)
        if task is None:
            continue
        output_bytes = metric_match.group("output_bytes")
        measurements.append(
            Measurement(
                format=format_name,
                implementation=implementation,
                dataset=current_dataset,
                task=task,
                input_bytes=current_input_bytes,
                output_bytes=int(output_bytes) if output_bytes is not None else None,
                milliseconds=float(metric_match.group("milliseconds")),
                reported_mib_s=float(metric_match.group("reported_mib")),
                run=run,
            )
        )

    return measurements


def build_command(
    format_name: str,
    implementation: str,
    zig: str,
    optimize: str | None,
) -> list[str]:
    """One command per (format, implementation); the Zig side needs no other
    selector."""
    if format_name == "json":
        if implementation == "jsonz":
            step, build_args = "bench-json-jsonz", []
        elif implementation == "std.json":
            step, build_args = "bench-json-std", []
        else:
            step, build_args = "bench-json", []
    else:
        step = {
            "serde": "bench-msgpack-serde",
            "msgpack.zig": "bench-msgpack-msgpack-zig",
            "zig-msgpack": "bench-msgpack-zig-msgpack",
        }[implementation]
        build_args = []
    command = [zig, "build", step, *build_args]
    if optimize:
        command.append(f"-Doptimize={optimize}")
    return command


def expected_measurements(format_name: str, implementation: str) -> set[tuple[str, str]]:
    return {
        (dataset, TASK_LABELS[token])
        for token in supported_tokens(format_name, implementation)
        for dataset in token_datasets(format_name, implementation, token)
    }


def validate_measurements(
    measurements: Sequence[Measurement],
    format_name: str,
    implementation: str,
) -> None:
    actual = [(item.dataset, item.task) for item in measurements]
    if len(actual) != len(set(actual)):
        raise RuntimeError(f"{format_name}/{implementation}: duplicate metric lines in benchmark output")

    expected = expected_measurements(format_name, implementation)
    actual_set = set(actual)
    missing = sorted(expected - actual_set)
    unexpected = sorted(actual_set - expected)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(f"{dataset}/{task}" for dataset, task in missing))
        if unexpected:
            details.append("unexpected " + ", ".join(f"{dataset}/{task}" for dataset, task in unexpected))
        raise RuntimeError(f"{format_name}/{implementation}: " + "; ".join(details))


def run_benchmarks(
    formats: Sequence[str],
    runs: int,
    zig: str,
    optimize: str,
    output_dir: Path,
) -> tuple[list[Measurement], dict[tuple[str, str], list[str]]]:
    all_measurements: list[Measurement] = []
    commands: dict[tuple[str, str], list[str]] = {}
    for format_name in formats:
        for implementation in implementations_for_format(format_name):
            raw_dir = output_dir / format_name / implementation_directory(implementation)
            command = build_command(format_name, implementation, zig, optimize)
            commands[(format_name, implementation)] = command
            collected = run_repetitions(
                label=f"{format_name}/{implementation}",
                command=command,
                raw_dir=raw_dir,
                stem="tasks",
                runs=runs,
                parse=lambda out, run, f=format_name: parse_output(out, f, run),
                validate=lambda meas, f=format_name, i=implementation: validate_measurements(meas, f, i),
            )
            all_measurements.extend(cast(list[Measurement], collected))
    return all_measurements, commands


def aggregate(measurements: Iterable[Measurement]) -> list[SummaryRow]:
    groups: dict[tuple[str, str, str, str], list[Measurement]] = {}
    for measurement in measurements:
        key = (measurement.format, measurement.implementation, measurement.dataset, measurement.task)
        groups.setdefault(key, []).append(measurement)

    rows: list[SummaryRow] = []
    for key in sorted(groups):
        format_name, implementation, dataset, task = key
        samples = groups[key]
        durations = [sample.milliseconds for sample in samples]
        measured_bytes = {sample.measured_bytes for sample in samples}
        if len(measured_bytes) != 1:
            raise RuntimeError(f"{format_name}/{task}/{dataset}: payload size changed between runs")
        median_ms = statistics.median(durations)
        measured = measured_bytes.pop()
        throughput_mib_s = measured / (median_ms / 1000.0) / (1024.0 * 1024.0)
        throughput_gb_s = measured / (median_ms / 1000.0) / 1_000_000_000.0
        rows.append(
            {
                "format": format_name,
                "implementation": implementation,
                "dataset": dataset,
                "task": task,
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
                "latency_us": median_ms * 1000.0,
                "ops_per_s": 1000.0 / median_ms,
            }
        )
    return rows


def write_csv(path: Path, rows: Sequence[SummaryRow]) -> None:
    fields = [
        "format",
        "implementation",
        "dataset",
        "task",
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
        "latency_us",
        "ops_per_s",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: Sequence[SummaryRow], runs: int) -> None:
    by_key = {
        (str(row["format"]), str(row["implementation"]), str(row["dataset"]), str(row["task"])): row
        for row in rows
    }

    lines = [
        "# zig-serde-bench results",
        "",
        (
            f"serde.zig {serde_version()} · median of {runs} process run(s). "
            "Throughput is computed from the median `ms/op` and the measured payload "
            "(encoded output bytes when reported, otherwise input bytes)."
        ),
        "",
    ]
    for format_name in FORMATS:
        for task, _ in TASKS:
            implementations = [
                implementation
                for implementation in implementations_for_format(format_name)
                if any((format_name, implementation, dataset, task) in by_key for dataset in ALL_DATASETS)
            ]
            if not implementations:
                continue
            task_datasets = [
                dataset
                for dataset in ALL_DATASETS
                if any((format_name, implementation, dataset, task) in by_key for implementation in implementations)
            ]
            headers = []
            for implementation in implementations:
                headers += [f"{implementation} GB/s", f"{implementation} \u00b5s/op", f"{implementation} ops/s"]
            lines.extend(
                [
                    f"## {FORMAT_LABELS[format_name]} · {task}",
                    "",
                    "| Dataset | " + " | ".join(headers) + " |",
                    "| --- | " + " | ".join("---:" for _ in headers) + " |",
                ]
            )
            for dataset in task_datasets:
                values: list[str] = []
                for implementation in implementations:
                    row = by_key.get((format_name, implementation, dataset, task))
                    if row is None:
                        values += ["-", "-", "-"]
                    else:
                        values += [
                            f"{float(row['throughput_gb_s']):.3f}",
                            f"{float(row['latency_us']):.2f}",
                            f"{float(row['ops_per_s']):,.0f}",
                        ]
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

    Layout: for each format, user task and metric (GB/s and ops/s) one column
    chart, with the participating implementations as separate series. No Python
    chart library.
    """
    by_key = {
        (str(row["format"]), str(row["implementation"]), str(row["dataset"]), str(row["task"])): row
        for row in rows
    }
    chart_counter = 0
    # metric key -> (axis title, decimals for tooltips)
    CHART_METRICS = (("gb", "GB/s", 3), ("ops", "ops/s", 0))

    def chart_html(
        datasets: list[str],
        series: list[dict[str, object]],
        height: int,
        filename: str,
        metric: str,
    ) -> str:
        nonlocal chart_counter
        chart_counter += 1
        container_id = f"chart-{chart_counter}"
        categories = [dataset.removesuffix(".json") for dataset in datasets]
        metric_title, decimals = next(
            (unit, dec) for key, unit, dec in CHART_METRICS if key == metric
        )
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
            "yAxis": {"title": {"text": metric_title}, "min": 0},
            "tooltip": {
                "shared": True,
                "pointFormat": f"{{series.name}}: <b>{{point.y:.{decimals}f}} {metric_title}</b><br/>",
            },
            "legend": {"layout": "horizontal", "align": "center", "verticalAlign": "top"},
            "plotOptions": {"column": {"borderRadius": 3, "pointPadding": 0.06, "groupPadding": 0.2}},
            "series": series,
        }
        return (
            f'<div id="{container_id}" class="hc-container"></div>\n'
            f"<script>Highcharts.chart({json.dumps(container_id)}, {json.dumps(options)});</script>\n"
        )

    def series_points(
        format_name: str,
        implementation: str,
        task: str,
        datasets: list[str],
        metric: str,
    ) -> list[float | None]:
        def value_for(dataset: str) -> float | None:
            row = by_key.get((format_name, implementation, dataset, task))
            if row is None:
                return None
            if metric == "gb":
                return float(row["throughput_gb_s"])
            if metric == "ops":
                return float(row["ops_per_s"])
            raise ValueError(f"unknown metric {metric!r}")

        return [value_for(dataset) for dataset in datasets]

    def collect_series(
        series_specs: list[tuple[str, str, str, str, list[str]]],
        metric: str,
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for name, format_name, implementation, task, datasets in series_specs:
            result.append(
                {
                    "name": name,
                    "data": series_points(format_name, implementation, task, datasets, metric),
                }
            )
        return result

    def card(heading: str, plot: str, description: str = "") -> str:
        detail = "" if not description else f'<p class="chart-note">{description}</p>\n'
        return "<section class=\"plot\">\n" f"<h2>{heading}</h2>\n{detail}{plot}\n" "</section>"

    sections: list[str] = []
    for format_name in FORMATS:
        label = FORMAT_LABELS[format_name]
        implementations = implementations_for_format(format_name)
        for task, token in TASKS:
            group = [
                dataset
                for dataset in ALL_DATASETS
                if any(
                    (format_name, implementation, dataset, task) in by_key
                    for implementation in implementations
                )
            ]
            if not group:
                continue
            implementations_present = [
                implementation
                for implementation in implementations
                if any(
                    (format_name, implementation, dataset, task) in by_key
                    for dataset in group
                )
            ]
            if not implementations_present:
                continue
            pair: list[str] = []
            for metric, unit, _ in CHART_METRICS:
                series = collect_series(
                    [
                        (
                            implementation,
                            format_name,
                            implementation,
                            task,
                            group,
                        )
                        for implementation in implementations_present
                    ],
                    metric,
                )
                pair.append(
                    card(
                        f"{label} · {task} · {unit}",
                        chart_html(
                            group,
                            series,
                            430,
                            f"{format_name}-{token}-{metric}",
                            metric,
                        ),
                    )
                )
            sections.append("<div class=\"plot-row\">\n" + "\n".join(pair) + "\n</div>")

    if not sections:
        raise RuntimeError("no measurements to chart for the selected formats")

    body = "\n".join(sections)
    note = (
        f"Median of {runs} process run(s) · serde.zig {serde_version()} · "
        "timed path excludes file loading and cleanup."
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>zig-serde-bench</title>
<script src="{HIGHCHARTS_CDN}highcharts.js"></script>
<script src="{HIGHCHARTS_CDN}modules/exporting.js"></script>
<script src="{HIGHCHARTS_CDN}modules/offline-exporting.js"></script>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px 32px 48px; background: #f3f6f9; color: #1f2733; }}
  header {{ max-width: 1000px; margin: 0 auto 22px; text-align: center; }}
  header h1 {{ margin: 0 0 6px; font-size: 24px; color: #141a23; }}
  header p  {{ margin: 0; color: #4a5568; font-size: 14px; }}
  h2 {{ font-size: 16px; margin: 0 0 12px; color: #141a23; }}
  .chart-note {{ margin: -4px 0 12px; color: #4a5568; font-size: 14px; }}
  section.plot {{ max-width: 1000px; margin: 0 auto 28px; background: #ffffff; border-radius: 12px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(16, 24, 40, 0.08); }}
  .plot-row {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 28px; max-width: 1320px; margin: 0 auto 28px; align-items: start; }}
  .plot-row section.plot {{ max-width: none; margin: 0; }}
  @media (max-width: 900px) {{ .plot-row {{ grid-template-columns: minmax(0, 1fr); }} }}
  .hc-container {{ width: 100%; }}
  footer {{ max-width: 1000px; margin: 4px auto 0; color: #718096; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<header>
  <h1>zig-serde-bench</h1>
  <p>{note}</p>
</header>
{body}
<footer>Generated by bench.py; raw logs and measurements.json live alongside this page.</footer>
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
        "--no-build",
        action="store_true",
        help="regenerate HTML, CSV, and Markdown from existing measurements",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    output_dir = DEFAULT_OUTPUT.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    formats = selected_values(args.format_name, FORMATS, "format")
    zig = os.environ.get("ZIG", "zig")
    optimize = "ReleaseFast"

    if args.no_build:
        measurements: list[Measurement] = []
        commands: dict[tuple[str, str], list[str]] = {}
        metadata: dict[str, object] = {}
    else:
        measurements, commands = run_benchmarks(formats, args.runs, zig, optimize, output_dir)
        metadata = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(ROOT),
            "serde": serde_version(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "runs": args.runs,
            "commands": {
                f"{format_name}/{implementation}": command
                for (format_name, implementation), command in commands.items()
            },
        }

    written: list[Path] = []
    open_pages: list[Path] = []
    for format_name in formats:
        format_dir = output_dir / format_name
        format_dir.mkdir(parents=True, exist_ok=True)
        if args.no_build:
            format_summary, runs = load_summary(format_dir / "measurements.json")
        else:
            runs = args.runs
            format_measurements = [item for item in measurements if item.format == format_name]
            format_summary = aggregate(format_measurements)
            payload = {
                "metadata": metadata | {"format": format_name},
                "measurements": [
                    asdict(item) | {"measured_bytes": item.measured_bytes}
                    for item in format_measurements
                ],
                "summary": format_summary,
            }
            (format_dir / "measurements.json").write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )

        csv_path = format_dir / "summary.csv"
        markdown_path = format_dir / "summary.md"
        page = format_dir / "index.html"
        write_csv(csv_path, format_summary)
        write_markdown(markdown_path, format_summary, runs)
        write_html_page(page, format_summary, runs)
        written.extend((csv_path, markdown_path, page))
        open_pages.append(page)

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
