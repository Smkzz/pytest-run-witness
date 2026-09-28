from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local pytest completion proof demo.")
    parser.add_argument("--output", type=Path, required=True, help="new or empty output directory")
    options = parser.parse_args(argv)

    script = Path(__file__).with_name("hosted_probe.py")
    commands = [
        [sys.executable, str(script), "produce", "--output", str(options.output)],
        [sys.executable, str(script), "verify", "--input", str(options.output)],
    ]
    started = time.perf_counter()
    for command in commands:
        result = subprocess.run(command, check=False)
        if result.returncode:
            return result.returncode
    elapsed = time.perf_counter() - started
    print(f"DEMO_ELAPSED_SECONDS={elapsed:.3f}")
    if elapsed > 120:
        print("DEMO_TIME_LIMIT=FAIL (over 120 seconds)", file=sys.stderr)
        return 1
    print("DEMO_TIME_LIMIT=PASS (under 120 seconds)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
