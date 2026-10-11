#!/usr/bin/env python3
"""The one step every BAMLAB job runs.

BAMLAB plans exist to give bam (the Bamboo CLI) real builds whose outcome is
known in advance; see lab/bamlab/README.md. This script is all they do: tick
for a while so there is a live log and a real duration, write JUnit results,
print the variables Bamboo passed, and pass or fail on purpose.

Standard library only, and self-contained rather than built on forgelab: it
needs no lab paths, and argparse's usage errors (exit 2) are the right shape
for a mistyped plan variable.
"""

import argparse
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

OUTCOMES = ("pass", "fail", "first-attempt-fails")
DEFAULT_STATE_DIR = Path.home() / ".bamlab" / "attempts"
JUNIT_FILE = "results.xml"
TESTS = ("compiles", "parses_config", "talks_to_bamboo")
FAILING = TESTS[1:]
# A restart follows its failed attempt within a session; a marker older than
# this is left from an earlier build numbering (plan recreated, Bamboo
# reinstalled), so it no longer counts.
MARKER_MAX_AGE_SECONDS = 24 * 60 * 60


def tick_count(seconds: int, tick: int) -> int:
    if seconds <= 0:
        return 0
    return math.ceil(seconds / tick)


def run_ticks(seconds, tick, sleep=time.sleep, now=datetime.now, out=print):
    """One flushed line per tick, so Bamboo's live log moves while it runs.

    The last sleep is cut short so the total never passes `seconds`.
    """
    count = tick_count(seconds, tick)
    for k in range(1, count + 1):
        out(f"{now():%H:%M:%S} bamlab tick {k}/{count}", flush=True)
        sleep(min(tick, seconds - (k - 1) * tick))


def junit_xml(passed: bool, plan: str) -> str:
    """Three test cases; when not passed, the last two fail."""
    classname = quoteattr(f"bamlab.{plan}")
    cases = []
    for name in TESTS:
        if not passed and name in FAILING:
            trace = f"AssertionError: {name} failed on purpose\n    at bamlab.{name}({plan})"
            cases.append(
                f'  <testcase classname={classname} name="{name}" time="0.1">'
                f'<failure message="deliberate failure: {name}">{escape(trace)}</failure>'
                "</testcase>"
            )
        else:
            cases.append(f'  <testcase classname={classname} name="{name}" time="0.1"/>')
    failures = 0 if passed else len(FAILING)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<testsuite name="bamlab" tests="{len(TESTS)}" failures="{failures}" errors="0">\n'
        + "\n".join(cases)
        + "\n</testsuite>\n"
    )


def first_attempt(state_dir: Path, key: str, now=time.time) -> bool:
    """True the first time a build key is seen; leaves a marker so a restart of
    the same build (Bamboo keeps the key) sees False. A stale marker counts as
    unseen."""
    marker = state_dir / key
    stamp = now()
    if marker.exists() and stamp - marker.stat().st_mtime <= MARKER_MAX_AGE_SECONDS:
        return False
    state_dir.mkdir(parents=True, exist_ok=True)
    marker.touch()
    os.utime(marker, (stamp, stamp))
    return True


def passes(outcome: str, state_dir: Path, key) -> bool:
    if outcome == "pass":
        return True
    if outcome == "fail":
        return False
    return not first_attempt(state_dir, key)


def bamboo_vars(env) -> list:
    return sorted(f"{name}={value}" for name, value in env.items() if name.startswith("bamboo_"))


def parse(argv):
    parser = argparse.ArgumentParser(prog="lab_step.py")
    parser.add_argument("--plan", default="BAMLAB")
    parser.add_argument("--sleep", type=int, default=0)
    parser.add_argument("--tick", type=int, default=2)
    parser.add_argument("--junit", choices=("pass", "fail", "auto"))
    parser.add_argument("--junit-dir", default="lab-results")
    parser.add_argument("--outcome", choices=OUTCOMES, default="pass")
    parser.add_argument("--attempt-key")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--show-vars", action="store_true")
    args = parser.parse_args(argv)
    if args.tick < 1:
        parser.error("--tick must be at least 1")
    if args.outcome == "first-attempt-fails" and not args.attempt_key:
        parser.error("--outcome first-attempt-fails needs --attempt-key")
    if args.attempt_key is not None and (not args.attempt_key or "/" in args.attempt_key):
        parser.error("--attempt-key must be a non-empty name without '/'")
    return args


def main(argv, env=None, sleep=time.sleep, now=datetime.now) -> int:
    args = parse(argv)
    env = os.environ if env is None else env
    # Decided first, so a build cancelled mid-sleep still counts as attempted.
    passed = passes(args.outcome, args.state_dir, args.attempt_key)
    if args.show_vars:
        for line in bamboo_vars(env):
            print(line)
    run_ticks(args.sleep, args.tick, sleep=sleep, now=now)
    if args.junit:
        junit_passed = passed if args.junit == "auto" else args.junit == "pass"
        results = Path(args.junit_dir)
        results.mkdir(parents=True, exist_ok=True)
        (results / JUNIT_FILE).write_text(junit_xml(junit_passed, args.plan))
    if passed:
        print(f"bamlab: {args.plan} passed")
        return 0
    print(f"ERROR bamlab: deliberate failure ({args.plan})", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
