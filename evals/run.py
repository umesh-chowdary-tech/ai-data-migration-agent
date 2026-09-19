"""One command for the whole evaluation:

    python -m evals.run              # rules-only golden eval + all test suites -> EVALUATION.md
    python -m evals.run --with-ai    # ...plus the golden eval again with the AI providers from .env

Datasets
  canonical      data/samples (the one the agent was developed against - its scores are not evidence on their own)
  dev-1..5       seeds 101-105: held-out round 1. Exposed a bug -> fixed -> these became development data
  dev2-1..5      seeds 201-205: held-out round 2. Exposed another bug -> fixed -> development data too
  final-1..5     seeds 301-305: generated after the last fix, never used to change anything
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / "var" / "eval"
RESULTS = ROOT / "evals" / "results"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-ai", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT))
    from scripts.generate_data import OUT, generate

    WORK.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    if not (OUT / "v1" / "ground_truth.json").exists():
        generate("canonical", 7, OUT, randomised=False)
    sets = [str(OUT)]
    for prefix, base in (("dev", 100), ("dev2", 200), ("final", 300)):
        for n in range(1, 6):
            folder = WORK / "datasets" / f"{prefix}-{n}"
            generate(f"{prefix}-{n}", base + n, folder, randomised=True)
            sets.append(str(folder))

    py = sys.executable
    print("== golden evaluation (rules-only)", flush=True)
    subprocess.run([py, "-m", "evals.golden", "--datasets", *sets, "--out", str(RESULTS / "rules-only.json")],
                   cwd=ROOT, check=True)
    if args.with_ai:
        print("== golden evaluation (with AI)", flush=True)
        ai_sets = [sets[0], *[s for s in sets if "final-" in s]]
        subprocess.run([py, "-m", "evals.golden", "--ai", "--datasets", *ai_sets, "--out",
                        str(RESULTS / "with-ai.json")], cwd=ROOT, check=True)
    print("== test suites", flush=True)
    junit = WORK / "junit.xml"
    subprocess.run([py, "-m", "pytest", "tests", "-q", "-p", "no:warnings", f"--junitxml={junit}"], cwd=ROOT)
    from evals.report import build
    (ROOT / "EVALUATION.md").write_text(build(RESULTS, junit), encoding="utf-8")
    print(f"wrote {ROOT / 'EVALUATION.md'}")


if __name__ == "__main__":
    main()
