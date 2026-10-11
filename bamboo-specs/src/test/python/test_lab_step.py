"""lab_step.py: the one step every BAMLAB job runs."""

import os
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

import lab_step

FIXED = datetime(2026, 10, 11, 9, 30, 5)


def test_the_script_is_executable():
    # Bamboo runs it by path; without the x bit every BAMLAB job fails.
    assert os.access(Path(lab_step.__file__), os.X_OK)


def test_tick_count_rounds_up_and_handles_zero():
    assert lab_step.tick_count(0, 2) == 0
    assert lab_step.tick_count(6, 2) == 3
    assert lab_step.tick_count(5, 2) == 3


def test_ticks_print_one_line_each_and_never_sleep_past_the_total():
    lines, slept = [], []
    lab_step.run_ticks(
        5, 2, sleep=slept.append, now=lambda: FIXED,
        out=lambda line, **kw: lines.append(line),
    )
    assert lines == [
        "09:30:05 bamlab tick 1/3",
        "09:30:05 bamlab tick 2/3",
        "09:30:05 bamlab tick 3/3",
    ]
    assert slept == [2, 2, 1]


def test_no_ticks_for_a_zero_sleep():
    lines = []
    lab_step.run_ticks(0, 2, sleep=lambda s: None, now=lambda: FIXED,
                       out=lambda line, **kw: lines.append(line))
    assert lines == []


@pytest.mark.parametrize("passed, failures", [(True, 0), (False, 2)])
def test_junit_has_three_tests_and_the_right_failure_count(passed, failures):
    suite = ET.fromstring(lab_step.junit_xml(passed, "BAMLAB-OK"))
    assert suite.get("tests") == "3"
    assert suite.get("failures") == str(failures)
    assert len(suite.findall("testcase")) == 3
    assert len(suite.findall("testcase/failure")) == failures


def test_a_failing_test_carries_a_message_and_a_multi_line_body():
    suite = ET.fromstring(lab_step.junit_xml(False, "BAMLAB-FLAKY"))
    failure = suite.find("testcase/failure")
    assert failure.get("message").startswith("deliberate failure: ")
    assert "\n" in failure.text
    assert "BAMLAB-FLAKY" in failure.text


def test_first_attempt_fails_then_a_restart_of_the_same_build_passes(tmp_path):
    state = tmp_path / "missing" / "attempts"  # does not exist yet
    assert lab_step.first_attempt(state, "BAMLAB-FLAKY-JOB1-7") is True
    assert lab_step.first_attempt(state, "BAMLAB-FLAKY-JOB1-7") is False


def test_markers_are_independent_per_build(tmp_path):
    assert lab_step.first_attempt(tmp_path, "BAMLAB-FLAKY-JOB1-7") is True
    assert lab_step.first_attempt(tmp_path, "BAMLAB-FLAKY-JOB1-8") is True


@pytest.mark.parametrize("outcome, expected", [("pass", True), ("fail", False)])
def test_fixed_outcomes_ignore_the_marker(tmp_path, outcome, expected):
    assert lab_step.passes(outcome, tmp_path, None) is expected
    assert list(tmp_path.iterdir()) == []


def test_show_vars_lists_only_bamboo_variables_sorted():
    env = {"PATH": "/bin", "bamboo_release": "1.2", "bamboo_env": "dev"}
    assert lab_step.bamboo_vars(env) == ["bamboo_env=dev", "bamboo_release=1.2"]


def test_main_passes_and_writes_junit(tmp_path, capsys):
    code = lab_step.main(
        ["--plan", "BAMLAB-OK", "--junit", "pass", "--junit-dir", str(tmp_path / "r")],
        env={}, sleep=lambda s: None, now=lambda: FIXED,
    )
    assert code == 0
    assert ET.parse(tmp_path / "r" / "results.xml").getroot().get("failures") == "0"
    assert capsys.readouterr().out.splitlines()[-1] == "bamlab: BAMLAB-OK passed"


def test_main_fails_with_a_recognisable_last_line(capsys):
    code = lab_step.main(["--plan", "BAMLAB-FAIL", "--outcome", "fail"],
                         env={}, sleep=lambda s: None, now=lambda: FIXED)
    assert code == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        "ERROR bamlab: deliberate failure (BAMLAB-FAIL)")


def test_main_junit_auto_follows_the_first_attempt_outcome(tmp_path):
    argv = ["--plan", "BAMLAB-FLAKY", "--outcome", "first-attempt-fails",
            "--attempt-key", "BAMLAB-FLAKY-JOB1-3", "--state-dir", str(tmp_path / "s"),
            "--junit", "auto", "--junit-dir", str(tmp_path / "r")]
    results = tmp_path / "r" / "results.xml"

    assert lab_step.main(argv, env={}, sleep=lambda s: None, now=lambda: FIXED) == 1
    assert ET.parse(results).getroot().get("failures") == "2"

    assert lab_step.main(argv, env={}, sleep=lambda s: None, now=lambda: FIXED) == 0
    assert ET.parse(results).getroot().get("failures") == "0"


def test_main_shows_vars_when_asked(capsys):
    lab_step.main(["--show-vars"], env={"bamboo_outcome": "pass", "HOME": "/h"},
                  sleep=lambda s: None, now=lambda: FIXED)
    assert "bamboo_outcome=pass" in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("argv", [
    ["--outcome", "PASS"],                       # a typo in the VARS plan variable
    ["--outcome", ""],
    ["--outcome", "first-attempt-fails"],        # no attempt key
    ["--outcome", "first-attempt-fails", "--attempt-key", "a/b"],
    ["--tick", "0"],
])
def test_bad_arguments_are_usage_errors(argv):
    with pytest.raises(SystemExit) as exit_:
        lab_step.main(argv, env={}, sleep=lambda s: None, now=lambda: FIXED)
    assert exit_.value.code == 2


def test_a_stale_marker_counts_as_a_first_attempt(tmp_path):
    # After a plan is recreated or Bamboo is reinstalled, build numbers start
    # again at 1; a marker left from the old numbering must not make a fresh
    # build pass.
    key = "BAMLAB-FLAKY-TEST-1"
    assert lab_step.first_attempt(tmp_path, key, now=lambda: 1_000.0) is True
    old = lab_step.MARKER_MAX_AGE_SECONDS + 1
    os.utime(tmp_path / key, (1_000.0, 1_000.0))
    assert lab_step.first_attempt(tmp_path, key, now=lambda: 1_000.0 + old) is True
    assert lab_step.first_attempt(tmp_path, key, now=lambda: 1_000.0 + old + 60) is False
