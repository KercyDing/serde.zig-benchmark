"""Shared benchmark engine.

All subprocess/build/run/collection logic lives here exactly once. Experiment
files (bench.py, and any sweep such as the parallel runner) only define the
commands to run and how to parse raw benchmark output.
"""

import argparse
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")

ROOT = Path(__file__).resolve().parent.parent
HIGHCHARTS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/highcharts/8.2.0/"


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
