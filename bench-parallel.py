#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Run serde.zig parallel scaling benchmarks and render an HTML report.

Examples::

    uv run bench-parallel.py
    uv run bench-parallel.py --format msgpack --thread 8 --runs 5
    uv run bench-parallel.py --plot-only
"""

from __future__ import annotations

import argparse
import json
import os
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
DEFAULT_OUTPUT = ROOT / "results" / "parallel"
FORMATS = ("json", "msgpack")
OPERATIONS = ("decode", "encode")
DATASETS = ("canada.json", "github_events.json", "poet.json", "twitter.json", "twitterescaped.json")
FORMAT_LABELS = {"json": "JSON", "msgpack": "MessagePack"}

DATASET_RE = re.compile(
    r"^\s*(?P<format>json|msgpack)\s+/\s+(?P<dataset>\S+)\s+\(\d+ input bytes,\s+\d+ encoded bytes,\s+\d+ repeats/worker\)\s*$"
)
RATE_RE = re.compile(
    r"^\s*(?P<threads>\d+) threads:\s+decode\s+(?P<decode>[0-9]+(?:\.[0-9]+)?) GB/s\s+\([^)]+\),\s+encode\s+(?P<encode>[0-9]+(?:\.[0-9]+)?) GB/s\s+\([^)]+\)\s*$"
)


@dataclass(frozen=True)
class Measurement:
    format: str
    dataset: str
    operation: str
    threads: int
    throughput_gb_s: float
    run: int


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def thread_counts(max_threads: int) -> tuple[int, ...]:
    counts = [1]
    while counts[-1] < max_threads:
        counts.append(min(counts[-1] * 2, max_threads))
    return tuple(counts)


def selected_formats(format_name: str) -> tuple[str, ...]:
    return FORMATS if format_name == "all" else (format_name,)


def build_command(args: argparse.Namespace, format_name: str) -> list[str]:
    if args.no_build:
        return [str(args.binary_dir / "parallel-bench"), str(args.thread), format_name]

    command = [
        args.zig,
        "build",
        "bench-parallel",
        f"-Dmax-threads={args.thread}",
        f"-Dformat={format_name}",
    ]
    if args.optimize:
        command.append(f"-Doptimize={args.optimize}")
    return command


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

    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        tail = "\n".join(output.splitlines()[-40:])
        raise RuntimeError(f"benchmark command failed with exit code {result.returncode}:\n  {' '.join(command)}\n{tail}")
    return output


def parse_output(output: str, run: int) -> list[Measurement]:
    current_format: str | None = None
    current_dataset: str | None = None
    measurements: list[Measurement] = []
    for line in output.splitlines():
        if dataset_match := DATASET_RE.match(line):
            current_format = dataset_match.group("format")
            current_dataset = dataset_match.group("dataset")
            continue
        if not (rate_match := RATE_RE.match(line)):
            continue
        if current_format is None or current_dataset is None:
            raise RuntimeError(f"found throughput before a dataset header: {line!r}")
        threads = int(rate_match.group("threads"))
        measurements.extend(
            (
                Measurement(current_format, current_dataset, "decode", threads, float(rate_match.group("decode")), run),
                Measurement(current_format, current_dataset, "encode", threads, float(rate_match.group("encode")), run),
            )
        )
    return measurements


def validate_measurements(measurements: Sequence[Measurement], formats: Sequence[str], max_threads: int) -> None:
    actual = {(item.format, item.dataset, item.operation, item.threads) for item in measurements}
    if len(actual) != len(measurements):
        raise RuntimeError("duplicate metric lines in benchmark output")
    expected = {
        (format_name, dataset, operation, threads)
        for format_name in formats
        for dataset in DATASETS
        for operation in OPERATIONS
        for threads in thread_counts(max_threads)
    }
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(f"{key[0]}/{key[1]}/{key[2]}/{key[3]}" for key in missing))
        if unexpected:
            details.append("unexpected " + ", ".join(f"{key[0]}/{key[1]}/{key[2]}/{key[3]}" for key in unexpected))
        raise RuntimeError("parallel benchmark output: " + "; ".join(details))


def aggregate(measurements: Iterable[Measurement]) -> list[dict[str, object]]:
    groups: dict[tuple[str, str, str, int], list[float]] = {}
    for measurement in measurements:
        key = (measurement.format, measurement.dataset, measurement.operation, measurement.threads)
        groups.setdefault(key, []).append(measurement.throughput_gb_s)
    return [
        {
            "format": format_name,
            "dataset": dataset,
            "operation": operation,
            "threads": threads,
            "runs": len(samples),
            "throughput_gb_s": statistics.median(samples),
            "min_gb_s": min(samples),
            "max_gb_s": max(samples),
        }
        for (format_name, dataset, operation, threads), samples in sorted(groups.items())
    ]


HIGHCHARTS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/highcharts/8.2.0/"


def write_html_page(path: Path, rows: Sequence[dict[str, object]], formats: Sequence[str], runs: int, max_threads: int) -> None:
    by_key = {
        (str(row["format"]), str(row["dataset"]), str(row["operation"]), int(row["threads"])): row
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

    def values(format_name: str, dataset: str, operation: str) -> list[float]:
        return [float(by_key[(format_name, dataset, operation, threads)]["throughput_gb_s"]) for threads in counts]

    overview = []
    for format_name in formats:
        for operation in OPERATIONS:
            overview.append(
                {
                    "name": f"{FORMAT_LABELS[format_name]} / {operation}",
                    "data": [statistics.geometric_mean(values(format_name, dataset, operation)[index] for dataset in DATASETS) for index in range(len(counts))],
                }
            )

    sections = [chart("Parallel throughput overview", overview, "parallel-overview")]
    for format_name in formats:
        for operation in OPERATIONS:
            series = [
                {"name": dataset.removesuffix(".json"), "data": values(format_name, dataset, operation)}
                for dataset in DATASETS
            ]
            sections.append(chart(f"{FORMAT_LABELS[format_name]} — {operation}", series, f"parallel-{format_name}-{operation}"))

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
<header><h1>serde.zig parallel scaling benchmark</h1><p>Median of {runs} process run(s) · typed JSON and MessagePack corpora · total throughput across workers.</p></header>
{''.join(sections)}
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--format", dest="format_name", choices=("json", "msgpack", "all"), default="all", help="benchmark one format or both (default: all)")
    result.add_argument("--thread", type=positive_int, default=os.cpu_count() or 1, help="maximum worker threads (default: all logical CPUs)")
    result.add_argument("--runs", type=positive_int, default=10, help="independent process runs (default: 10)")
    result.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help=f"directory for index.html, measurements.json, and raw logs (default: {DEFAULT_OUTPUT})")
    result.add_argument("--zig", default=os.environ.get("ZIG", "zig"), help="Zig executable (default: $ZIG or zig)")
    result.add_argument("--optimize", default="ReleaseFast", help="optimization mode forwarded as -Doptimize (default: ReleaseFast)")
    result.add_argument("--no-build", action="store_true", help="run zig-out/bin/parallel-bench instead of invoking zig build")
    result.add_argument("--binary-dir", type=Path, default=ROOT / "zig-out" / "bin", help="directory containing parallel-bench for --no-build")
    result.add_argument("--plot-only", action="store_true", help="rebuild index.html from saved measurements.json")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "measurements.json"

    if args.plot_only:
        if not summary_path.is_file():
            raise SystemExit(f"--plot-only requires {summary_path}")
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        summary = payload["summary"]
        runs = int(payload["metadata"]["runs"])
        max_threads = int(payload["metadata"]["max_threads"])
        formats = tuple(payload["metadata"]["formats"])
    else:
        measurements: list[Measurement] = []
        formats = selected_formats(args.format_name)
        commands: dict[str, list[str]] = {}
        for format_name in formats:
            command = build_command(args, format_name)
            commands[format_name] = command
            raw_dir = output_dir / format_name
            raw_dir.mkdir(parents=True, exist_ok=True)
            for run in range(1, args.runs + 1):
                print(f"[{format_name}] run {run}/{args.runs}: {' '.join(command)}", flush=True)
                output = run_process(command)
                raw_path = raw_dir / f"parallel-{run:02d}.txt"
                raw_path.write_text(output, encoding="utf-8")
                parsed = parse_output(output, run)
                validate_measurements(parsed, (format_name,), args.thread)
                measurements.extend(parsed)
                print(f"  parsed {len(parsed)} measurements -> {raw_path}", flush=True)
        summary = aggregate(measurements)
        runs = args.runs
        max_threads = args.thread
        payload = {
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "runs": runs,
                "max_threads": max_threads,
                "formats": formats,
                "commands": commands,
            },
            "measurements": [asdict(measurement) for measurement in measurements],
            "summary": summary,
        }
        summary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    page = output_dir / "index.html"
    write_html_page(page, summary, formats, runs, max_threads)
    print(f"wrote {summary_path}")
    print(f"wrote {page}")
    webbrowser.open(page.resolve().as_uri())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"bench-parallel.py: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
