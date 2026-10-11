# BAMLAB Test Plans Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `BAMLAB` Bamboo project: eight fast synthetic plans with fixed, known outcomes (including a parent that triggers child plans and a grandchild) so bam's features can be tried against real Bamboo; plus make `make bamboo` name the process holding a port it cannot forward.

**Architecture:** One spec class, `lab/bamlab/BamLabSpec.java`, builds and publishes all eight plans in dependency order; every job runs one stdlib Python script, `lab/bamlab/scripts/lab_step.py`, whose flags choose duration, JUnit output, and outcome. The existing `publish_specs.py` discovers the class from the `lab/*/*Spec.java` layout, so publishing needs no change.

**Tech Stack:** Bamboo Specs 12.1.8 (Java 17, Maven, JUnit 4), Python 3 standard library, pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-bamlab-test-plans-design.md`

**Tracking:** [r0jjames/forge-lab#1](https://github.com/r0jjames/forge-lab/issues/1)

## Global Constraints

- Bamboo project key `BAMLAB`, name `bam test lab`; plan keys exactly `TREE`, `OK`, `LEAF`, `FLAKY`, `SLOW`, `ORPHAN`, `VARS`, `FAIL`.
- Publish order exactly: LEAF, OK, FLAKY, SLOW, ORPHAN, VARS, FAIL, TREE.
- Child plans: TREE → OK, FLAKY, SLOW; OK → LEAF. No other dependencies.
- No plan has a trigger. No job has an `agent.role` requirement except FLAKY's Test job (`agent.role=host`).
- Every plan links the `forge-lab` repository (`SpecConstants.REPO_NAME`) and every job checks it out first.
- `lab_step.py` is standard library only and executable (`chmod +x`, mode 100755 in git).
- JUnit results go to `lab-results/results.xml`; the parser task is a **final** task on OK's and FLAKY's Test jobs only, with result directory `lab-results/*.xml`.
- The FLAKY marker lives in `~/.bamlab/attempts/<attempt key>`; the attempt key is `${bamboo.buildResultKey}`.
- Every deliberate failure prints `ERROR bamlab: deliberate failure (<plan>)` as its last line and exits 1.
- forge-lab commits: Conventional Commits, Roj's identity only, no `Co-Authored-By` or other Claude attribution. Every commit body ends `Refs #1`; the PR body says `Closes #1`.
- Run commands from the worktree root: `/Users/roj/Dev/projects/forge-lab/.worktrees/bamlab-plans`.

## Review Focus

- **The script is not executable in git.** Bamboo runs `bamboo-specs/.../lab_step.py` directly, so a 100644 file fails every job with "permission denied". Task 1 adds a test that the file is executable, and Step 7 checks the git mode.
- **The `outcome` variable gets a value the script doesn't accept** (`PASS`, `ok`, empty). This must be a clear usage error (exit 2), not a traceback and not a silent pass. Task 1 tests it.
- **The marker directory doesn't exist yet** on a fresh agent (`~/.bamlab` missing). The first FLAKY run must create it rather than crash. Task 1 tests a nested directory that doesn't exist.
- **`--sleep 0`, or a duration that isn't a multiple of the tick.** There must be no ZeroDivisionError and no overshoot past the requested seconds. Task 1 tests both.
- **`lsof` is missing, or finds nothing.** `make bamboo` must still die with the old "already in use" message, not crash with FileNotFoundError. Task 4 tests both.

---

### Task 1: `lab_step.py`, the step every BAMLAB job runs

**Files:**
- Create: `bamboo-specs/src/main/java/lab/bamlab/scripts/lab_step.py`
- Create: `bamboo-specs/src/test/python/test_lab_step.py`
- Modify: `bamboo-specs/src/test/python/conftest.py` (add the scripts dir to `sys.path`)

**Interfaces:**
- Produces (CLI, used by Task 2's inline script bodies):
  `lab_step.py [--plan KEY] [--sleep N] [--tick S] [--junit pass|fail|auto] [--junit-dir DIR] [--outcome pass|fail|first-attempt-fails] [--attempt-key KEY] [--state-dir DIR] [--show-vars]`
  Defaults: `--plan BAMLAB`, `--sleep 0`, `--tick 2`, `--junit-dir lab-results`, `--outcome pass`, `--state-dir ~/.bamlab/attempts`. `--junit auto` means "fail if the outcome failed, else pass". Exit 0 on pass, 1 on a deliberate failure, 2 on a usage error.
- Produces (Python, for tests): `tick_count(seconds: int, tick: int) -> int`, `run_ticks(seconds, tick, sleep, now, out) -> None`, `junit_xml(passed: bool, plan: str) -> str`, `first_attempt(state_dir: Path, key: str) -> bool`, `passes(outcome: str, state_dir: Path, key: str | None) -> bool`, `bamboo_vars(env: Mapping[str, str]) -> list[str]`, `main(argv, env=None, sleep=time.sleep, now=datetime.now) -> int`.

- [ ] **Step 1: Put the scripts dir on the test path**

In `bamboo-specs/src/test/python/conftest.py`, add one line to the tuple of paths, after `LAB / "specspublish" / "scripts",`:

```python
    LAB / "bamlab" / "scripts",
```

- [ ] **Step 2: Write the failing tests**

Create `bamboo-specs/src/test/python/test_lab_step.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest -q bamboo-specs/src/test/python/test_lab_step.py`
Expected: collection error `ModuleNotFoundError: No module named 'lab_step'`.

- [ ] **Step 4: Write the script**

Create `bamboo-specs/src/main/java/lab/bamlab/scripts/lab_step.py`:

```python
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


def first_attempt(state_dir: Path, key: str) -> bool:
    """True the first time a build key is seen; leaves a marker so a restart of
    the same build (Bamboo keeps the key) sees False."""
    marker = state_dir / key
    if marker.exists():
        return False
    state_dir.mkdir(parents=True, exist_ok=True)
    marker.touch()
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
```

Then make it executable:

```bash
chmod +x bamboo-specs/src/main/java/lab/bamlab/scripts/lab_step.py
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest -q bamboo-specs/src/test/python/test_lab_step.py`
Expected: all pass (21 tests, counting parametrized cases).

- [ ] **Step 6: Run it once by hand**

Run: `bamboo-specs/src/main/java/lab/bamlab/scripts/lab_step.py --plan BAMLAB-FAIL --sleep 2 --tick 1 --outcome fail; echo "exit=$?"`
Expected: two tick lines, then `ERROR bamlab: deliberate failure (BAMLAB-FAIL)`, then `exit=1`.

- [ ] **Step 7: Commit, checking the mode**

```bash
git add bamboo-specs/src/main/java/lab/bamlab/scripts/lab_step.py bamboo-specs/src/test/python/test_lab_step.py bamboo-specs/src/test/python/conftest.py
git ls-files -s bamboo-specs/src/main/java/lab/bamlab/scripts/lab_step.py   # must start 100755
git commit -m "feat: add lab_step.py, the step every BAMLAB job runs"
```

---

### Task 2: `BamLabSpec`, the eight plans and their wiring

**Files:**
- Create: `bamboo-specs/src/main/java/lab/bamlab/BamLabSpec.java`
- Create: `bamboo-specs/src/test/java/lab/bamlab/BamLabSpecTest.java`
- Modify: `docs/superpowers/specs/2026-10-11-bamlab-test-plans-design.md` (§4 flag table: `--junit pass|fail|auto`)

**Interfaces:**
- Consumes: Task 1's CLI flags, exactly as listed there.
- Produces: `List<Plan> BamLabSpec.plans()` (package-private, publish order); `public static void main(String[])` publishes them in that order. `static final String STEP` is the repo-relative script path.

- [ ] **Step 1: Write the failing test**

Create `bamboo-specs/src/test/java/lab/bamlab/BamLabSpecTest.java`:

```java
package lab.bamlab;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import com.atlassian.bamboo.specs.api.model.plan.JobProperties;
import com.atlassian.bamboo.specs.api.model.plan.PlanProperties;
import com.atlassian.bamboo.specs.api.model.plan.dependencies.DependenciesProperties;
import com.atlassian.bamboo.specs.api.model.task.TaskProperties;
import com.atlassian.bamboo.specs.api.model.VariableProperties;
import com.atlassian.bamboo.specs.api.util.EntityPropertiesBuilders;
import com.atlassian.bamboo.specs.model.task.ScriptTaskProperties;
import com.atlassian.bamboo.specs.model.task.TestParserTaskProperties;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.Test;

public class BamLabSpecTest {

    private static List<PlanProperties> built() {
        return new BamLabSpec().plans().stream()
                .map(EntityPropertiesBuilders::build)
                .collect(Collectors.toList());
    }

    private static String key(PlanProperties plan) {
        return plan.getProject().getKey().getKey() + "-" + plan.getKey().getKey();
    }

    private static Map<String, PlanProperties> byKey() {
        Map<String, PlanProperties> plans = new LinkedHashMap<>();
        for (PlanProperties plan : built()) {
            plans.put(key(plan), plan);
        }
        return plans;
    }

    private static Set<String> children(PlanProperties plan) {
        DependenciesProperties deps = plan.getDependenciesProperties();
        if (deps == null) {
            return Set.of();
        }
        return deps.getChildPlans().stream()
                .map(c -> c.getProjectKey().getKey() + "-" + c.getKey().getKey())
                .collect(Collectors.toSet());
    }

    private static List<JobProperties> jobs(PlanProperties plan) {
        return plan.getStages().stream()
                .flatMap(s -> s.getJobs().stream())
                .collect(Collectors.toList());
    }

    private static String scripts(JobProperties job) {
        return job.getTasks().stream()
                .filter(t -> t instanceof ScriptTaskProperties)
                .map(t -> ((ScriptTaskProperties) t).getBody())
                .collect(Collectors.joining("\n"));
    }

    @Test
    public void everyPlanIsOfflineValidAndInTheBamlabProject() {
        for (PlanProperties plan : built()) {
            assertEquals("BAMLAB", plan.getProject().getKey().getKey());
            assertEquals("bam test lab", plan.getProject().getName());
        }
    }

    @Test
    public void publishOrderIsLeavesFirst() {
        assertEquals(
                List.of("BAMLAB-LEAF", "BAMLAB-OK", "BAMLAB-FLAKY", "BAMLAB-SLOW",
                        "BAMLAB-ORPHAN", "BAMLAB-VARS", "BAMLAB-FAIL", "BAMLAB-TREE"),
                built().stream().map(BamLabSpecTest::key).collect(Collectors.toList()));
    }

    @Test
    public void everyChildIsPublishedBeforeItsParent() {
        // Bamboo refuses a dependency on a plan that does not exist yet.
        List<PlanProperties> plans = built();
        List<String> order = plans.stream().map(BamLabSpecTest::key).collect(Collectors.toList());
        for (int i = 0; i < plans.size(); i++) {
            for (String child : children(plans.get(i))) {
                int at = order.indexOf(child);
                assertTrue(child + " must be published before " + order.get(i), at >= 0 && at < i);
            }
        }
    }

    @Test
    public void treeAndOkTriggerExactlyTheirChildren() {
        Map<String, PlanProperties> plans = byKey();
        assertEquals(Set.of("BAMLAB-OK", "BAMLAB-FLAKY", "BAMLAB-SLOW"),
                children(plans.get("BAMLAB-TREE")));
        assertEquals(Set.of("BAMLAB-LEAF"), children(plans.get("BAMLAB-OK")));
        for (String other : List.of("BAMLAB-LEAF", "BAMLAB-FLAKY", "BAMLAB-SLOW",
                "BAMLAB-ORPHAN", "BAMLAB-VARS", "BAMLAB-FAIL")) {
            assertEquals(other + " triggers nothing", Set.of(), children(plans.get(other)));
        }
    }

    @Test
    public void nothingTriggersAPlanByItself() {
        for (PlanProperties plan : built()) {
            assertTrue(key(plan) + " must have no trigger", plan.getTriggers().isEmpty());
        }
    }

    @Test
    public void onlyFlakysTestJobNeedsTheHostAgent() {
        for (PlanProperties plan : built()) {
            for (JobProperties job : jobs(plan)) {
                boolean flakyTest = key(plan).equals("BAMLAB-FLAKY") && job.getName().equals("Test");
                if (flakyTest) {
                    assertEquals(1, job.getRequirements().size());
                    assertEquals("agent.role", job.getRequirements().get(0).getKey());
                    assertEquals("host", job.getRequirements().get(0).getMatchValue());
                } else {
                    assertTrue(key(plan) + "/" + job.getName() + " must run on any agent",
                            job.getRequirements().isEmpty());
                }
            }
        }
    }

    @Test
    public void junitParserIsAFinalTaskOnlyWhereResultsAreWritten() {
        Set<String> withParser = Set.of("BAMLAB-OK/Test", "BAMLAB-FLAKY/Test");
        for (PlanProperties plan : built()) {
            for (JobProperties job : jobs(plan)) {
                String where = key(plan) + "/" + job.getName();
                long parsers = job.getFinalTasks().stream()
                        .filter(t -> t instanceof TestParserTaskProperties).count();
                assertEquals(where, withParser.contains(where) ? 1 : 0, parsers);
                assertTrue(where + " must not parse tests outside final tasks",
                        job.getTasks().stream().noneMatch(t -> t instanceof TestParserTaskProperties));
                assertEquals(where + " writes JUnit iff it parses it",
                        withParser.contains(where), scripts(job).contains("--junit "));
            }
        }
    }

    @Test
    public void everyJobChecksOutThenRunsTheStep() {
        for (PlanProperties plan : built()) {
            for (JobProperties job : jobs(plan)) {
                List<TaskProperties> tasks = job.getTasks();
                assertTrue(tasks.get(0).getClass().getSimpleName().startsWith("VcsCheckout"));
                assertTrue(scripts(job).startsWith(BamLabSpec.STEP + " --plan ${bamboo.planKey}"));
            }
        }
    }

    @Test
    public void flakyTestJobKeysItsMarkerOnTheBuildResultKey() {
        JobProperties test = jobs(byKey().get("BAMLAB-FLAKY")).stream()
                .filter(j -> j.getName().equals("Test")).findFirst().orElseThrow();
        String body = scripts(test);
        assertTrue(body.contains("--outcome first-attempt-fails"));
        assertTrue(body.contains("--attempt-key ${bamboo.buildResultKey}"));
        assertTrue(body.contains("--junit auto"));
    }

    @Test
    public void varsPlanDeclaresTheVariablesBamResolves() {
        Map<String, String> vars = byKey().get("BAMLAB-VARS").getVariables().stream()
                .collect(Collectors.toMap(VariableProperties::getName, VariableProperties::getValue,
                        (a, b) -> a, LinkedHashMap::new));
        assertEquals(Map.of("env", "dev", "release", "", "db_password", "changeme",
                "outcome", "pass"), vars);
        String body = jobs(byKey().get("BAMLAB-VARS")).get(0).getTasks().stream()
                .filter(t -> t instanceof ScriptTaskProperties)
                .map(t -> ((ScriptTaskProperties) t).getBody()).findFirst().orElseThrow();
        assertTrue(body.contains("--show-vars"));
        assertTrue(body.contains("--outcome \"${bamboo.outcome}\""));
    }

    @Test
    public void slowHasThreeStagesOfAMinuteEach() {
        PlanProperties slow = byKey().get("BAMLAB-SLOW");
        assertEquals(3, slow.getStages().size());
        for (JobProperties job : jobs(slow)) {
            assertTrue(scripts(job).contains("--sleep 60 --tick 2"));
        }
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `mvn -f bamboo-specs/pom.xml -q test -Dtest=BamLabSpecTest`
Expected: compilation failure, `cannot find symbol: class BamLabSpec`.

- [ ] **Step 3: Write the spec class**

Create `bamboo-specs/src/main/java/lab/bamlab/BamLabSpec.java`:

```java
package lab.bamlab;

import com.atlassian.bamboo.specs.api.BambooSpec;
import com.atlassian.bamboo.specs.api.builders.BambooKey;
import com.atlassian.bamboo.specs.api.builders.Variable;
import com.atlassian.bamboo.specs.api.builders.plan.Job;
import com.atlassian.bamboo.specs.api.builders.plan.Plan;
import com.atlassian.bamboo.specs.api.builders.plan.PlanIdentifier;
import com.atlassian.bamboo.specs.api.builders.plan.Stage;
import com.atlassian.bamboo.specs.api.builders.plan.dependencies.Dependencies;
import com.atlassian.bamboo.specs.api.builders.project.Project;
import com.atlassian.bamboo.specs.api.builders.repository.VcsRepositoryIdentifier;
import com.atlassian.bamboo.specs.api.builders.requirement.Requirement;
import com.atlassian.bamboo.specs.builders.task.CheckoutItem;
import com.atlassian.bamboo.specs.builders.task.ScriptTask;
import com.atlassian.bamboo.specs.builders.task.TestParserTask;
import com.atlassian.bamboo.specs.builders.task.VcsCheckoutTask;
import com.atlassian.bamboo.specs.util.BambooServer;
import java.util.List;
import lab.shared.SpecConstants;

/**
 * BAMLAB: synthetic plans with known outcomes, for trying bam (the Bamboo CLI)
 * against a real Bamboo. See lab/bamlab/README.md and
 * docs/superpowers/specs/2026-10-11-bamlab-test-plans-design.md.
 *
 * <p>Every job runs lab_step.py; its flags are the whole behaviour. No plan has
 * a trigger: they run when bam (or a person) runs them, or when a parent
 * succeeds.
 */
@BambooSpec
public class BamLabSpec {

    static final String STEP = "bamboo-specs/src/main/java/lab/bamlab/scripts/lab_step.py";
    static final String PROJECT_KEY = "BAMLAB";

    private static Project project() {
        return new Project().key(new BambooKey(PROJECT_KEY)).name("bam test lab");
    }

    private static Plan plan(String key, String name, String description) {
        return new Plan(project(), name, new BambooKey(key))
                .description(description)
                .linkedRepositories(new VcsRepositoryIdentifier().name(SpecConstants.REPO_NAME));
    }

    private static PlanIdentifier child(String key) {
        return new PlanIdentifier(PROJECT_KEY, key);
    }

    /** A job that checks the repo out, then runs lab_step.py with these flags. */
    private static Job job(String name, String key, String flags) {
        return new Job(name, new BambooKey(key)).tasks(
                new VcsCheckoutTask().description("checkout")
                        .checkoutItems(new CheckoutItem().defaultRepository()),
                new ScriptTask().description("lab step")
                        .inlineBody(STEP + " --plan ${bamboo.planKey} " + flags));
    }

    /**
     * A job that also writes JUnit results. The parser is a final task so it
     * still reads them after the step has failed the job.
     */
    private static Job testJob(String name, String key, String flags) {
        return job(name, key, flags + " --junit-dir lab-results")
                .finalTasks(TestParserTask.createJUnitParserTask()
                        .resultDirectories("lab-results/*.xml"));
    }

    Plan leaf() {
        return plan("LEAF", "Leaf", "Grandchild of TREE through OK; passes in ~5s")
                .stages(new Stage("Run").jobs(job("Run", "RUN", "--sleep 5 --tick 1")));
    }

    Plan ok() {
        return plan("OK", "OK", "Child of TREE; passes with green tests and triggers LEAF")
                .stages(
                        new Stage("Build").jobs(job("Build", "BUILD", "--sleep 10 --tick 2")),
                        new Stage("Test").jobs(testJob("Test", "TEST", "--sleep 10 --tick 2 --junit pass")))
                .dependencies(new Dependencies().childPlans(child("LEAF")));
    }

    Plan flaky() {
        return plan("FLAKY", "Flaky",
                "Child of TREE; each build fails its first attempt and passes when restarted")
                .stages(
                        new Stage("Build").jobs(job("Build", "BUILD", "--sleep 5 --tick 1")),
                        new Stage("Test").jobs(
                                testJob("Test", "TEST", "--sleep 5 --tick 1 --junit auto "
                                        + "--outcome first-attempt-fails "
                                        + "--attempt-key ${bamboo.buildResultKey}")
                                        // The attempt marker lives in the agent's home; the
                                        // host agent's home persists across restarts, the
                                        // containerized agent's may not.
                                        .requirements(new Requirement("agent.role")
                                                .matchValue("host")
                                                .matchType(Requirement.MatchType.EQUALS))));
    }

    Plan slow() {
        return plan("SLOW", "Slow", "Child of TREE; three one-minute stages with a live log")
                .stages(
                        new Stage("Stage 1").jobs(job("Stage 1", "ONE", "--sleep 60 --tick 2")),
                        new Stage("Stage 2").jobs(job("Stage 2", "TWO", "--sleep 60 --tick 2")),
                        new Stage("Stage 3").jobs(job("Stage 3", "THREE", "--sleep 60 --tick 2")));
    }

    Plan orphan() {
        return plan("ORPHAN", "Orphan",
                "Never triggered; listed under TREE in bam's children config to show 'not run'")
                .stages(new Stage("Run").jobs(job("Run", "RUN", "--sleep 3 --tick 1")));
    }

    Plan vars() {
        return plan("VARS", "Vars", "Prints the variables it received; outcome from 'outcome'")
                .variables(
                        new Variable("env", "dev"),
                        new Variable("release", ""),
                        // Bamboo masks it by name; never a real secret.
                        new Variable("db_password", "changeme"),
                        new Variable("outcome", "pass"))
                .stages(new Stage("Run").jobs(
                        job("Run", "RUN", "--show-vars --outcome \"${bamboo.outcome}\"")));
    }

    Plan fail() {
        return plan("FAIL", "Fail", "Always fails with an error tail and no tests")
                .stages(new Stage("Run").jobs(job("Run", "RUN", "--sleep 3 --tick 1 --outcome fail")));
    }

    Plan tree() {
        return plan("TREE", "Tree", "Parent: on success triggers OK, FLAKY and SLOW")
                .stages(
                        new Stage("Prepare").jobs(job("Prepare", "PREP", "--sleep 4 --tick 2")),
                        new Stage("Fan-out").jobs(
                                job("Left", "LEFT", "--sleep 6 --tick 2"),
                                job("Right", "RIGHT", "--sleep 6 --tick 2")))
                .dependencies(new Dependencies().childPlans(
                        child("OK"), child("FLAKY"), child("SLOW")));
    }

    /** Publish order: Bamboo refuses a dependency on a plan that does not exist yet. */
    List<Plan> plans() {
        return List.of(leaf(), ok(), flaky(), slow(), orphan(), vars(), fail(), tree());
    }

    public static void main(String[] args) {
        BambooServer server = new BambooServer(SpecConstants.BAMBOO_URL);
        for (Plan plan : new BamLabSpec().plans()) {
            server.publish(plan);
        }
    }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `mvn -f bamboo-specs/pom.xml -q test -Dtest=BamLabSpecTest`
Expected: BUILD SUCCESS, 11 tests run, 0 failures. If `getDependenciesProperties()` returns a non-null object with an empty `getChildPlans()` for plans without dependencies, the `children` helper already handles it. If the build rejects `Dependencies` without `.configuration(...)`, add `.configuration(new DependenciesConfiguration())` (import `com.atlassian.bamboo.specs.api.builders.plan.dependencies.DependenciesConfiguration`) to both `Dependencies` and rerun.

- [ ] **Step 5: Check discovery picks it up**

Run: `python3 -c "import sys; sys.path.insert(0,'bamboo-specs/src/main/java/lab/specspublish/scripts'); import publish_specs, pathlib; print(publish_specs.spec_classes(pathlib.Path('bamboo-specs/src/main/java/lab')))"`
Expected: the list includes `lab.bamlab.BamLabSpec`.

- [ ] **Step 6: Amend the spec's flag table**

In `docs/superpowers/specs/2026-10-11-bamlab-test-plans-design.md` §4, change the row

```
| `--junit pass\|fail --junit-dir DIR` | writes `DIR/results.xml`: 3 test cases in one suite; in `fail` mode 2 fail, each with a message and a multi-line body |
```

to

```
| `--junit pass\|fail\|auto --junit-dir DIR` | writes `DIR/results.xml`: 3 test cases in one suite; in `fail` mode 2 fail, each with a message and a multi-line body; `auto` fails them exactly when the outcome fails |
```

- [ ] **Step 7: Commit**

```bash
git add bamboo-specs/src/main/java/lab/bamlab/BamLabSpec.java bamboo-specs/src/test/java/lab/bamlab/BamLabSpecTest.java docs/superpowers/specs/2026-10-11-bamlab-test-plans-design.md
git commit -m "feat: define the BAMLAB plans, a parent with child plans and a grandchild"
```

---

### Task 3: README for bam users, and pointers to it

**Files:**
- Create: `bamboo-specs/src/main/java/lab/bamlab/README.md`
- Modify: `bamboo-specs/src/main/java/lab/README.md` (layout tree)
- Modify: `README.md` (day-to-day guides paragraph)
- Modify: `CLAUDE.md` (layout map)

**Interfaces:**
- Consumes: plan keys and behaviour from Task 2, flags from Task 1.

- [ ] **Step 1: Write `lab/bamlab/README.md`**

Create `bamboo-specs/src/main/java/lab/bamlab/README.md`:

````markdown
# bamlab/ — BAMLAB, plans for trying bam

Synthetic plans whose outcome is known before they run, so every feature of
[bam](https://github.com/r0jjames/bam-cli) can be tried against a real Bamboo
in seconds. They touch no VMs. Every job runs `scripts/lab_step.py`; its flags
are the whole behaviour. Design:
`docs/superpowers/specs/2026-10-11-bamlab-test-plans-design.md`.

Published with every other plan (`make specs-publish`, the pre-push hook,
`FORGE-SPECS`). No plan has a trigger.

## Plans

| Plan | Does | Try with bam |
| --- | --- | --- |
| `BAMLAB-TREE` | passes in ~10 s, then triggers OK, FLAKY, SLOW | `build tree`, tree `--watch`, TUI `t` |
| `BAMLAB-OK` | passes in ~20 s with 3 green tests, then triggers LEAF | stats, estimates, smoke runs, sets |
| `BAMLAB-LEAF` | passes in ~5 s | grandchild in the tree |
| `BAMLAB-FLAKY` | first attempt of each build fails (2 failing tests); a restart passes | `build rerun`, `--rerun-failed`, failed tests |
| `BAMLAB-SLOW` | three 60 s stages, a log line every 2 s | `build cancel`, `watch`, TUI live logs |
| `BAMLAB-ORPHAN` | passes; nothing triggers it | `not run` node via `children:` |
| `BAMLAB-VARS` | prints the variables it got; `outcome` decides pass/fail | variables, `--from`, `--edit`, `target add` |
| `BAMLAB-FAIL` | always fails, no tests | `logs`, exit code 1, set rerun |

```
BAMLAB-TREE
├── BAMLAB-OK
│   └── BAMLAB-LEAF
├── BAMLAB-FLAKY   (fails on its first attempt)
└── BAMLAB-SLOW    (~3 min)
```

## bam config

Put this in the `.bam.yaml` of any directory you drive bam from (the server
alias is whatever you named this Bamboo in `bam server add`):

```yaml
version: 1
projects: [BAMLAB]
children:
  BAMLAB-TREE: [BAMLAB-OK, BAMLAB-FLAKY, BAMLAB-SLOW, BAMLAB-ORPHAN]
  BAMLAB-OK: [BAMLAB-LEAF]
targets:
  smoke:
    plan: BAMLAB-OK
  vars:
    plan: BAMLAB-VARS
    required: [release]
    options:
      env: [dev, stage, prod]
    defaults:
      release: "${BAMLAB_RELEASE}"
  fail:
    plan: BAMLAB-FAIL
sets:
  lab:
    stages:
      - [smoke]
      - [fail]
```

A `children` entry makes `ORPHAN` show as `not run`, and bam counts a
`not run` child as a failed tree; drop `BAMLAB-ORPHAN` from the list to see a
tree that can go fully green after a FLAKY rerun. Field reference: bam's
`docs/configuration.md`.

## Feature checklist

| Feature | Command | Expect |
| --- | --- | --- |
| tree | `bam run BAMLAB-TREE --watch`, then `bam build tree <key it printed> --watch` | TREE ✓, OK ✓ → LEAF ✓, FLAKY ✗ (2 tests), SLOW running, ORPHAN not run |
| rerun to green | `bam build tree <TREE key> --rerun-failed` | FLAKY restarts and passes |
| logs | `bam logs BAMLAB-FAIL` | tail ends `ERROR bamlab: deliberate failure (BAMLAB-FAIL)` |
| failed tests | `bam build show <FLAKY key>` | 2 failed tests, `parses_config` and `talks_to_bamboo` |
| cancel | `bam run BAMLAB-SLOW`, then `bam build cancel <key>` | build stopped |
| live logs | `bam`, open BAMLAB-SLOW's running build, `l` | tick lines arrive every 2 s |
| required vars | `bam run vars` with `BAMLAB_RELEASE` unset | usage error naming `release`, nothing triggered |
| options | `bam run vars --var release=1 --var env=qa` | usage error listing dev, stage, prod |
| masking | `bam run vars --var release=1 --watch`, then `bam logs` | `db_password` shown as `********` |
| exit codes | `bam run BAMLAB-FAIL --watch; echo $?` | `1` |

## The FLAKY marker

`lab_step.py` remembers a FLAKY build's first attempt in
`~/.bamlab/attempts/<build result key>` on the agent, so a restart of the same
build passes. The Test job therefore requires `agent.role=host`. If the marker
is gone (another agent, a wiped home), the restart fails again. Clear old
markers with:

```bash
rm -rf ~/.bamlab/attempts
```
````

- [ ] **Step 2: Point the other docs at it**

In `bamboo-specs/src/main/java/lab/README.md`, in the layout tree, replace

```
└── specspublish/               # FORGE-SPECS
```

with

```
├── specspublish/               # FORGE-SPECS
└── bamlab/                     # BAMLAB-* (plans for trying bam)
    ├── BamLabSpec.java
    └── scripts/lab_step.py
```

and keep the two lines under `specspublish/` that follow it, re-indented with `│   ` instead of four spaces so the tree stays connected.

In `README.md`, after the line ending `Keycloak, HDFS, OpenSearch and k9s once a cluster is up.`, add a paragraph:

```markdown

[`bamboo-specs/src/main/java/lab/bamlab/README.md`](bamboo-specs/src/main/java/lab/bamlab/README.md)
covers `BAMLAB`, synthetic plans with known outcomes for trying the `bam` CLI
against this Bamboo.
```

In `CLAUDE.md`'s layout map, after the `lab/shared/` bullet (the one ending `` `terraform/` (`modules/multipass/` = swappable VM backend boundary), `ansible/` ``), add:

```markdown
- `lab/bamlab/` — `BAMLAB-*`: synthetic plans with fixed outcomes (a parent
  with child plans, a first-attempt-fails child, a slow one, a failing one) for
  trying bam against this Bamboo. One spec class publishes all eight, leaves
  first; every job runs `scripts/lab_step.py`. See its README
```

- [ ] **Step 3: Commit**

```bash
git add bamboo-specs/src/main/java/lab/bamlab/README.md bamboo-specs/src/main/java/lab/README.md README.md CLAUDE.md
git commit -m "docs: document BAMLAB and how to try each bam feature on it"
```

---

### Task 4: `make bamboo` names whatever holds a port it cannot forward

**Files:**
- Modify: `infra/scripts/start_lab.py` (`decide_forward`, new `parse_lsof` and `port_holder`)
- Test: `bamboo-specs/src/test/python/test_start_lab.py`

**Interfaces:**
- Produces: `parse_lsof(text: str) -> str` (pure; `"bamboostub (pid 40237)"` or `""`), `port_holder(port: int, run_out=proc.run_out) -> str`, and `decide_forward(states: dict, holder=port_holder) -> str` (same return values as today).

- [ ] **Step 1: Write the failing tests**

Append to `bamboo-specs/src/test/python/test_start_lab.py`:

```python
LSOF = "p40237\ncbamboostub\nf3\n"


def test_parses_the_process_holding_a_port_from_lsof():
    assert start_lab.parse_lsof(LSOF) == "bamboostub (pid 40237)"


def test_parses_nothing_from_empty_lsof_output():
    assert start_lab.parse_lsof("") == ""


def test_a_half_forwarded_pair_names_the_process_holding_the_port():
    with pytest.raises(LabError) as err:
        start_lab.decide_forward({8085: True, 54663: False},
                                 holder=lambda port: "bamboostub (pid 40237)")
    assert "8085" in str(err.value)
    assert "bamboostub (pid 40237)" in str(err.value)


def test_port_holder_is_empty_when_lsof_is_missing():
    def no_lsof(*args, **kwargs):
        raise FileNotFoundError("lsof")
    assert start_lab.port_holder(8085, run_out=no_lsof) == ""


def test_port_holder_asks_lsof_for_the_listener():
    calls = []
    def fake(*args, **kwargs):
        calls.append(args)
        return LSOF
    assert start_lab.port_holder(8085, run_out=fake) == "bamboostub (pid 40237)"
    assert calls == [("lsof", "-nP", "-iTCP:8085", "-sTCP:LISTEN", "-Fpc")]


def test_a_half_forwarded_pair_without_a_known_holder_keeps_the_old_message():
    with pytest.raises(LabError, match="port 8085 is already in use"):
        start_lab.decide_forward({8085: True, 54663: False}, holder=lambda port: "")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest -q bamboo-specs/src/test/python/test_start_lab.py`
Expected: FAIL. `parse_lsof` / `port_holder` don't exist, and `decide_forward` takes no `holder` argument.

- [ ] **Step 3: Implement**

In `infra/scripts/start_lab.py`, replace the whole `decide_forward` function with:

```python
def parse_lsof(text: str) -> str:
    """`lsof -Fpc` output to "name (pid N)" for the first process, or ""."""
    pid = name = ""
    for line in text.splitlines():
        if line.startswith("p") and not pid:
            pid = line[1:]
        elif line.startswith("c") and pid and not name:
            name = line[1:]
    return f"{name} (pid {pid})" if pid and name else ""


def port_holder(port: int, run_out=proc.run_out) -> str:
    """Who is listening on the port, for the error message. Best effort: no
    lsof, or nothing found, is an empty string, never a new failure."""
    try:
        return parse_lsof(run_out(
            "lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc", check=False,
        ))
    except OSError:
        return ""


def decide_forward(states: dict, holder=port_holder) -> str:
    """Reuse a `make ui` already running in another terminal, or start our own.

    A half-open pair is neither: something else holds one of the ports, and a
    second forward would fail to bind it. Say which port, and which process
    when lsof can tell, rather than letting kubectl's "address already in use"
    arrive later, mid-startup.
    """
    if all(states.values()):
        return "reuse"
    if not any(states.values()):
        return "start"
    taken = [port for port, is_open in states.items() if is_open]
    held = [f"{port} is held by {who}" for port in taken if (who := holder(port))]
    if held:
        proc.die(
            f"port {'; port '.join(held)}, but the rest of the forward is not up — "
            "stop it and retry"
        )
    proc.die(
        f"port {', '.join(str(port) for port in taken)} is already in use but the rest "
        "of the forward is not — stop whatever holds it (a dead 'make ui'?) and retry"
    )
```

Note: `port_holder` passes `check=False` through to `proc.run_out`; the test's fake accepts it via `**kwargs` and records only the positional `args`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest -q bamboo-specs/src/test/python/test_start_lab.py`
Expected: all pass, including the existing `test_refuses_a_half_forwarded_pair`. That test calls `decide_forward` without `holder`, so it runs real `lsof`; its assertion only checks that "8085" appears, which holds either way.

- [ ] **Step 5: Commit**

```bash
git add infra/scripts/start_lab.py bamboo-specs/src/test/python/test_start_lab.py
git commit -m "fix: make bamboo names the process holding a port it cannot forward"
```

---

### Task 5: Verify the whole branch, then try it on Bamboo

**Files:** none changed unless a check fails.

- [ ] **Step 1: Full offline suite**

Run: `pytest -q bamboo-specs/src/test/python && mvn -f bamboo-specs/pom.xml -q test`
Expected: pytest all pass (the earlier 360 plus this branch's tests), Maven BUILD SUCCESS.

- [ ] **Step 2: Lint the way the repo does**

Run: `make lint`
Expected: success. If a host tool `make lint` needs is missing (terraform, ansible-lint), report which, and treat Step 1 as the check that covers this branch's files.

- [ ] **Step 3: Publish and run on a real Bamboo (manual, needs the lab up)**

This needs Bamboo running and nothing else on port 8085. If `bamboostub` holds it, stop that `make stub` first. Then:

```bash
make bamboo            # terminal 1: stack, forward, host agent
make specs-publish     # terminal 2
```

Expected: the publish log shows `lab.bamlab.BamLabSpec`, and Bamboo lists the eight `BAMLAB` plans. Then, in Bamboo or with bam:
1. Run `BAMLAB-TREE`. OK, FLAKY and SLOW start after it; LEAF starts after OK.
2. FLAKY fails, with 2 failed tests and the `ERROR bamlab` tail.
3. Restart FLAKY's failed build. It passes.
4. ORPHAN never runs.

If step 3 does not pass, read the FLAKY Test job's log for `bamboo_buildResultKey` (run once with `--show-vars` added) and report whether Bamboo kept the key across the restart; that is the spec's one assumption.

- [ ] **Step 4: Report and hand over**

Report the results of Steps 1–3. Then use superpowers:finishing-a-development-branch to integrate `feat/bamlab-plans` (forge-lab is a personal repo whose history merges to `main` directly; ask which the owner wants).
