"""Run serde.zig parallel scaling benchmarks and render an HTML report.

Examples::

    uv run bench/bench-parallel.py
    uv run bench/bench-parallel.py --format msgpack --thread 8 --runs 5
    uv run bench/bench-parallel.py --plot-only
"""

import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import webbrowser
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

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


def build_command(args: argparse.Namespace, format_name: str, implementation: str, mode: str) -> list[str]:
    command = [
        args.zig,
        "build",
        "bench-parallel",
        f"-Dmax-threads={args.thread}",
        f"-Dformat={format_name}",
        f"-Dimplementation={implementation_argument(implementation)}",
        f"-Dmode={mode}",
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
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"could not start {' '.join(command)}: {exc}") from exc

    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        tail = "\n".join(output.splitlines()[-40:])
        raise RuntimeError(f"benchmark command failed with exit code {result.returncode}:\n  {' '.join(command)}\n{tail}")
    return output


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


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--format", dest="format_name", choices=("json", "msgpack", "all"), default="all", help="benchmark one format or both (default: all)")
    result.add_argument("--mode", choices=("generic", "typed", "all"), default="typed", help="representation(s) to benchmark (default: typed)")
    result.add_argument("--thread", type=positive_int, default=os.cpu_count() or 1, help="maximum worker threads (default: all logical CPUs)")
    result.add_argument("--runs", type=positive_int, default=10, help="independent process runs (default: 10)")
    result.add_argument("--output", dest="exports", action="append", choices=("csv", "md"), default=None, help="write only the chosen summary file(s), without the page (repeatable)")
    result.add_argument("--no-plot", action="store_true", help="write summary.csv and summary.md without the page")
    result.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help=f"directory for index.html, measurements.json, and raw logs (default: {DEFAULT_OUTPUT})")
    result.add_argument("--zig", default=os.environ.get("ZIG", "zig"), help="Zig executable (default: $ZIG or zig)")
    result.add_argument("--optimize", default="ReleaseFast", help="optimization mode forwarded as -Doptimize (default: ReleaseFast)")
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
        summary = cast(list[SummaryRow], [{**row, "mode": row.get("mode", "typed")} for row in payload["summary"]])
        runs = int(payload["metadata"]["runs"])
        max_threads = int(payload["metadata"]["max_threads"])
        saved_formats = tuple(payload["metadata"]["formats"])
        formats = tuple(format_name for format_name in saved_formats if format_name in selected_formats(args.format_name))
        saved_modes = tuple(payload["metadata"].get("modes", ("typed",)))
        modes = tuple(mode for mode in saved_modes if args.mode == "all" or mode == args.mode)
        if not formats:
            raise SystemExit(f"--format {args.format_name} has no saved parallel measurements")
        summary = [row for row in summary if str(row["format"]) in formats and str(row["mode"]) in modes]
    else:
        measurements: list[Measurement] = []
        formats = selected_formats(args.format_name)
        modes = ("generic", "typed") if args.mode == "all" else (args.mode,)
        commands: dict[str, list[str]] = {}
        for format_name in formats:
            for implementation in implementations_for_format(format_name):
                for mode in modes:
                    command = build_command(args, format_name, implementation, mode)
                    commands[f"{format_name}/{implementation}/{mode}"] = command
                    raw_dir = output_dir / format_name / implementation_directory(implementation)
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    for run in range(1, args.runs + 1):
                        print(f"[{format_name}/{implementation}/{mode}] run {run}/{args.runs}: {' '.join(command)}", flush=True)
                        output = run_process(command)
                        raw_path = raw_dir / f"{mode}-parallel-{run:02d}.txt"
                        raw_path.write_text(output, encoding="utf-8")
                        parsed = parse_output(output, run, mode)
                        validate_measurements(parsed, format_name, implementation, mode, args.thread)
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
                "modes": modes,
                "commands": commands,
            },
            "measurements": [asdict(measurement) for measurement in measurements],
            "summary": summary,
        }
        summary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if args.exports:
        want_csv = "csv" in args.exports
        want_md = "md" in args.exports
        want_page = False
    elif args.no_plot:
        want_csv, want_md, want_page = True, True, False
    else:
        want_csv, want_md, want_page = True, True, True

    written: list[Path] = [] if args.plot_only else [summary_path]
    if want_csv:
        csv_path = output_dir / "summary.csv"
        write_csv(csv_path, summary)
        written.append(csv_path)
    if want_md:
        markdown_path = output_dir / "summary.md"
        write_markdown(markdown_path, summary, formats, modes, max_threads)
        written.append(markdown_path)
    page: Path | None = None
    if want_page:
        page = output_dir / "index.html"
        assert page is not None
        write_html_page(page, summary, formats, modes, runs, max_threads)
        written.append(page)

    for path in written:
        print(f"wrote {path}")
    if page is not None:
        webbrowser.open(page.resolve().as_uri())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"bench-parallel.py: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
