"""Scaling experiment (thread sweep) on the shared benchmark engine.

Run from bench.py with ``--thread N``; writes scaling.csv / scaling.md /
scaling.html into the same results directory.
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys
import webbrowser
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast
from engine import thread_counts, run_repetitions

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "results" / "parallel"
FORMATS = ("json", "msgpack")
OPERATIONS = ("decode", "encode")
DATASETS = ("canada.json", "citm_catalog.json", "fgo.json", "github_events.json", "gsoc-2018.json", "lottie.json", "otfcc.json", "poet.json", "twitter.json", "twitterescaped.json")
TYPED_DATASETS = frozenset({"canada.json", "github_events.json", "poet.json", "twitter.json", "twitterescaped.json"})
FORMAT_LABELS = {"json": "JSON", "msgpack": "MessagePack"}
IMPLEMENTATIONS = ("serde", "std.json")


def implementations_for_format(format_name: str) -> tuple[str, ...]:
    return IMPLEMENTATIONS if format_name == "json" else ("serde",)


def implementation_argument(implementation: str) -> str:
    return "std" if implementation == "std.json" else implementation


def implementation_directory(implementation: str) -> str:
    return "serde.zig" if implementation == "serde" else "std"

DATASET_RE = re.compile(
    r"^\s*(?P<implementation>serde|std\.json)\s+/\s+(?P<format>json|msgpack)\s+/\s+(?P<dataset>\S+)\s+\(\d+ input bytes,\s+\d+ encoded bytes,\s+\d+ repeats/worker\)\s*$"
)
RATE_RE = re.compile(
    r"^\s*(?P<threads>\d+) threads:\s+decode\s+(?P<decode>[0-9]+(?:\.[0-9]+)?) GB/s\s+\([^)]+\),\s+encode\s+(?P<encode>[0-9]+(?:\.[0-9]+)?) GB/s\s+\([^)]+\)\s*$"
)


@dataclass(frozen=True)
class Measurement:
    format: str
    implementation: str
    mode: str
    dataset: str
    operation: str
    threads: int
    throughput_gb_s: float
    run: int


class SummaryRow(TypedDict):
    format: str
    implementation: str
    mode: str
    dataset: str
    operation: str
    threads: int
    runs: int
    throughput_gb_s: float
    min_gb_s: float
    max_gb_s: float


def selected_formats(format_name: str) -> tuple[str, ...]:
    return FORMATS if format_name == "all" else (format_name,)


def build_command(max_threads: int, format_name: str, implementation: str, mode: str) -> list[str]:
    return [
        os.environ.get("ZIG", "zig"),
        "build",
        "bench-parallel",
        f"-Dmax-threads={max_threads}",
        f"-Dformat={format_name}",
        f"-Dimplementation={implementation_argument(implementation)}",
        f"-Dmode={mode}",
        "-Doptimize=ReleaseFast",
    ]



def parse_output(output: str, run: int, mode: str) -> list[Measurement]:
    current_format: str | None = None
    current_implementation: str | None = None
    current_dataset: str | None = None
    measurements: list[Measurement] = []
    for line in output.splitlines():
        if dataset_match := DATASET_RE.match(line):
            current_format = dataset_match.group("format")
            current_implementation = dataset_match.group("implementation")
            current_dataset = dataset_match.group("dataset")
            continue
        if not (rate_match := RATE_RE.match(line)):
            continue
        if current_format is None or current_implementation is None or current_dataset is None:
            raise RuntimeError(f"found throughput before a dataset header: {line!r}")
        threads = int(rate_match.group("threads"))
        measurements.extend(
            (
                Measurement(current_format, current_implementation, mode, current_dataset, "decode", threads, float(rate_match.group("decode")), run),
                Measurement(current_format, current_implementation, mode, current_dataset, "encode", threads, float(rate_match.group("encode")), run),
            )
        )
    return measurements


def datasets_for_mode(mode: str) -> tuple[str, ...]:
    return tuple(dataset for dataset in DATASETS if mode == "generic" or dataset in TYPED_DATASETS)


def validate_measurements(measurements: Sequence[Measurement], format_name: str, implementation: str, mode: str, max_threads: int) -> None:
    actual = {(item.format, item.implementation, item.mode, item.dataset, item.operation, item.threads) for item in measurements}
    if len(actual) != len(measurements):
        raise RuntimeError("duplicate metric lines in benchmark output")
    expected = {
        (format_name, implementation, mode, dataset, operation, threads)
        for dataset in datasets_for_mode(mode)
        for operation in OPERATIONS
        for threads in thread_counts(max_threads)
    }
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join("/".join(map(str, key)) for key in missing))
        if unexpected:
            details.append("unexpected " + ", ".join("/".join(map(str, key)) for key in unexpected))
        raise RuntimeError("parallel benchmark output: " + "; ".join(details))


def aggregate(measurements: Iterable[Measurement]) -> list[SummaryRow]:
    groups: dict[tuple[str, str, str, str, str, int], list[float]] = {}
    for measurement in measurements:
        key = (measurement.format, measurement.implementation, measurement.mode, measurement.dataset, measurement.operation, measurement.threads)
        groups.setdefault(key, []).append(measurement.throughput_gb_s)
    return [
        {
            "format": format_name,
            "implementation": implementation,
            "mode": mode,
            "dataset": dataset,
            "operation": operation,
            "threads": threads,
            "runs": len(samples),
            "throughput_gb_s": statistics.median(samples),
            "min_gb_s": min(samples),
            "max_gb_s": max(samples),
        }
        for (format_name, implementation, mode, dataset, operation, threads), samples in sorted(groups.items())
    ]


def write_csv(path: Path, rows: Sequence[SummaryRow]) -> None:
    fields = [
        "format",
        "implementation",
        "mode",
        "dataset",
        "operation",
        "threads",
        "runs",
        "throughput_gb_s",
        "min_gb_s",
        "max_gb_s",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: Sequence[SummaryRow], formats: Sequence[str], modes: Sequence[str], max_threads: int) -> None:
    by_key = {
        (str(row["format"]), str(row["implementation"]), str(row["mode"]), str(row["dataset"]), str(row["operation"]), int(row["threads"])): row
        for row in rows
    }
    lines = ["# serde.zig parallel benchmark results", ""]
    for format_name in formats:
        for implementation in implementations_for_format(format_name):
            for mode in modes:
                datasets = datasets_for_mode(mode)
                for operation in OPERATIONS:
                    lines.extend(
                        [
                            f"## {FORMAT_LABELS[format_name]} / {implementation} / {mode} / {operation}",
                            "",
                            "| Threads | " + " | ".join(dataset.removesuffix(".json") for dataset in datasets) + " |",
                            "| ---: | " + " | ".join("---:" for _ in datasets) + " |",
                        ]
                    )
                    for threads in thread_counts(max_threads):
                        values = []
                        for dataset in datasets:
                            row = by_key[(format_name, implementation, mode, dataset, operation, threads)]
                            values.append(f"{float(row['throughput_gb_s']):.3f} GB/s")
                        lines.append(f"| {threads} | " + " | ".join(values) + " |")
                    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


HIGHCHARTS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/highcharts/8.2.0/"


def write_html_page(path: Path, rows: Sequence[SummaryRow], formats: Sequence[str], modes: Sequence[str], runs: int, max_threads: int) -> None:
    by_key = {
        (str(row["format"]), str(row["implementation"]), str(row["mode"]), str(row["dataset"]), str(row["operation"]), int(row["threads"])): row
        for row in rows
    }
    counts = thread_counts(max_threads)
    chart_id = 0

    def chart(title: str, series: list[dict[str, object]], filename: str) -> str:
        nonlocal chart_id
        chart_id += 1
        container_id = f"chart-{chart_id}"
        options = {
            "chart": {"type": "line", "height": 390, "backgroundColor": "transparent"},
            "title": {"text": None},
            "credits": {"enabled": False},
            "exporting": {"filename": filename},
            "xAxis": {"categories": counts, "title": {"text": "worker threads"}},
            "yAxis": {"min": 0, "title": {"text": "total throughput (GB/s)"}},
            "tooltip": {"shared": True, "valueDecimals": 3, "valueSuffix": " GB/s"},
            "legend": {"layout": "horizontal", "align": "center", "verticalAlign": "top"},
            "plotOptions": {"series": {"marker": {"enabled": True}, "lineWidth": 2}},
            "series": series,
        }
        return f'<section class="plot"><h2>{title}</h2><div id="{container_id}"></div></section>\n<script>Highcharts.chart({json.dumps(container_id)}, {json.dumps(options)});</script>'

    def values(format_name: str, implementation: str, mode: str, dataset: str, operation: str) -> list[float]:
        return [float(by_key[(format_name, implementation, mode, dataset, operation, threads)]["throughput_gb_s"]) for threads in counts]

    overview = []
    for format_name in formats:
        for implementation in implementations_for_format(format_name):
            for mode in modes:
                for operation in OPERATIONS:
                    overview.append(
                        {
                            "name": f"{FORMAT_LABELS[format_name]} / {implementation} / {mode} / {operation}",
                            "data": [statistics.geometric_mean(values(format_name, implementation, mode, dataset, operation)[index] for dataset in datasets_for_mode(mode)) for index in range(len(counts))],
                        }
                    )

    sections = [chart("Parallel throughput overview", overview, "parallel-overview")]
    for format_name in formats:
        for mode in modes:
            for operation in OPERATIONS:
                series = [
                    {
                        "name": f"{implementation} / {dataset.removesuffix('.json')}",
                        "data": values(format_name, implementation, mode, dataset, operation),
                    }
                    for implementation in implementations_for_format(format_name)
                    for dataset in datasets_for_mode(mode)
                ]
                sections.append(chart(f"{FORMAT_LABELS[format_name]} / {mode} — {operation}", series, f"parallel-{format_name}-{mode}-{operation}"))

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>serde.zig parallel benchmark</title>
<script src="{HIGHCHARTS_CDN}highcharts.js"></script>
<script src="{HIGHCHARTS_CDN}modules/exporting.js"></script>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 24px 32px 48px; background: #f3f6f9; color: #1f2733; }}
  header, section.plot {{ max-width: 1000px; margin-left: auto; margin-right: auto; }}
  header {{ margin-bottom: 22px; }}
  h1 {{ margin: 0 0 6px; font-size: 24px; color: #141a23; }}
  header p {{ margin: 0; color: #4a5568; font-size: 14px; }}
  h2 {{ font-size: 16px; margin: 0 0 12px; color: #141a23; }}
  section.plot {{ margin-bottom: 28px; background: #fff; border-radius: 12px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(16, 24, 40, .08); }}
</style>
</head>
<body>
<header><h1>serde.zig parallel scaling benchmark</h1><p>Median of {runs} process run(s) · JSON compares serde.zig and std.json · total throughput across workers.</p></header>
{''.join(sections)}
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def run_scaling(
    *,
    formats: Sequence[str],
    runs: int,
    max_threads: int,
    output_dir: Path,
    exports: Sequence[str] | None = None,
) -> tuple[list[Path], Path | None]:
    """Thread sweep for the given formats. Returns written paths and the page path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "scaling-measurements.json"
    measurements: list[Measurement] = []
    commands: dict[str, list[str]] = {}
    modes = ("generic", "typed")
    for format_name in formats:
        for implementation in implementations_for_format(format_name):
            for mode in modes:
                command = build_command(max_threads, format_name, implementation, mode)
                commands[f"{format_name}/{implementation}/{mode}"] = command
                raw_dir = output_dir / "parallel" / implementation_directory(implementation)
                collected = run_repetitions(
                    label=f"scale/{format_name}/{implementation}/{mode}",
                    command=command,
                    raw_dir=raw_dir,
                    stem=f"{mode}-parallel",
                    runs=runs,
                    parse=lambda out, run, m=mode: parse_output(out, run, m),
                    validate=lambda meas, f=format_name, i=implementation, m=mode, mt=max_threads: validate_measurements(meas, f, i, m, mt),
                )
                measurements.extend(cast(list[Measurement], collected))
    summary = aggregate(measurements)
    payload = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runs": runs,
            "max_threads": max_threads,
            "formats": list(formats),
            "modes": list(modes),
            "commands": commands,
        },
        "measurements": [asdict(measurement) for measurement in measurements],
        "summary": summary,
    }
    summary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    written: list[Path] = [summary_path]
    want_csv = exports is None or "csv" in exports
    want_md = exports is None or "md" in exports
    want_page = exports is None
    if want_csv:
        csv_path = output_dir / "scaling.csv"
        write_csv(csv_path, summary)
        written.append(csv_path)
    if want_md:
        md_path = output_dir / "scaling.md"
        write_markdown(md_path, summary, formats, modes, max_threads)
        written.append(md_path)
    page_path: Path | None = None
    if want_page:
        page_path = output_dir / "scaling.html"
        write_html_page(page_path, summary, formats, modes, runs, max_threads)
        written.append(page_path)
    return written, page_path
