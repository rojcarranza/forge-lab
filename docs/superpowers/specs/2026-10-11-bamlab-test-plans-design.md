# BAMLAB: Bamboo plans that exercise bam's features

## Problem

bam (the Bamboo CLI in `r0jjames/bam-cli`) keeps adding features that only
mean something against real Bamboo behaviour: `bam build tree` walks the
builds a parent plan triggered, `--rerun-failed` restarts the topmost
failures, the terminal UI streams a running stage's log, `bam logs` reads a
failed job's tail and its failing tests. bam's unit tests replay fixtures,
and the fixtures were recorded from plans that exist nowhere in code. The
plans forge-lab has today (`FORGE-PROV`, `FORGE-DEPROV`, `FORGE-SPECS`,
`AGENT-BUILD`) are slow, have no child plans, and never fail on purpose.
So each new bam feature is built against a guess of how Bamboo behaves.

## Goal

A set of fast, synthetic plans in forge-lab, defined as Bamboo Specs like
every other plan here, whose outcomes are fixed and known, so that:

- a developer can try any bam feature against real Bamboo in seconds, and
- bam's e2e suite and fixture recorder can point at them (spec #2, in
  bam-cli, written after this lab has been published and run once).

Out of scope: changes to bam-cli; making the real provisioning plans into
parents of child plans; any plan that touches VMs or terraform.

## Decisions

| Topic | Choice | Why |
| --- | --- | --- |
| Realism | Synthetic plans only (`echo`, `sleep`, generated JUnit) | Seconds per run, safe to trigger repeatedly from an e2e suite |
| Outcomes | Fixed per plan, except one plan that fails its first attempt | Deterministic asserts; the exception makes rerun-to-green real |
| Child failure control | Fixed in the child plan | Bamboo child plans do not inherit the parent's trigger-time variables, so a child cannot be told at trigger time to fail |
| Project | New Bamboo project `BAMLAB`, "bam test lab" | bam's `projects:` scope can name it alone; `FORGE` stays the real lab |
| Agent | No `agent.role` requirement, except `BAMLAB-FLAKY` | Runs on whichever agent is up; FLAKY needs a persistent home (§4) |
| Code | One spec class publishing every plan, one stdlib Python step script | Matches `lab/<planid>/` layout and the CI-agnostic-core rule |

## 1. Layout

```
bamboo-specs/src/main/java/lab/bamlab/
├── BamLabSpec.java        # every BAMLAB plan
├── README.md              # plan table, bam config snippet, feature checklist
└── scripts/lab_step.py    # the only thing any BAMLAB job runs
bamboo-specs/src/test/java/lab/bamlab/BamLabSpecTest.java
bamboo-specs/src/test/python/test_lab_step.py
```

`publish_specs.py` discovers `lab/*/*Spec.java`, so `BamLabSpec` is
published by `make specs-publish`, the pre-push hook and `FORGE-SPECS` with
no other change. One class publishing several plans is allowed: the
discovery unit is the class, not the plan.

## 2. Plans

Every plan checks out the `forge-lab` linked repository (so
`bam run --revision` and `--revision from` act on real commits), has no
repository or schedule trigger, and keeps plan branches off.

| Plan | Stages → jobs | Behaviour | bam features it serves |
| --- | --- | --- | --- |
| `BAMLAB-TREE` | Prepare → (Prepare); Fan-out → (Left, Right) in parallel | passes in ~10 s; on success triggers OK, FLAKY, SLOW | `build tree`, tree `--watch`, TUI tree and plan screen children |
| `BAMLAB-OK` | Build → Test | passes in ~20 s, 3 passing JUnit tests; triggers LEAF | nested children, `plan stats`, estimates, smoke e2e, sets |
| `BAMLAB-LEAF` | Run | passes in ~5 s | grandchild depth in the tree |
| `BAMLAB-FLAKY` | Build → Test | first attempt of each build fails (2 of 3 JUnit tests fail, error tail); a restart of that build passes | `build rerun`, `tree --rerun-failed`, `set rerun --failed`, `logs`, failed tests |
| `BAMLAB-SLOW` | Stage 1 → Stage 2 → Stage 3, one job each | ~60 s per stage, a log line every 2 s, passes | `build cancel`, `watch`, TUI live stage logs, progress bars |
| `BAMLAB-ORPHAN` | Run | passes; nothing triggers it | a `children:` entry bam shows as `not run` |
| `BAMLAB-VARS` | Run | prints the variables it received; outcome from the `outcome` variable | variable precedence, `--from`, `--edit`, `target add`/`sync`, `plan vars`, secret masking |
| `BAMLAB-FAIL` | Run | always fails with an error tail, no tests | e2e `-set-rerun-plan`, `logs`, exit code 1, the "no tests" path |

`BAMLAB-VARS` declares plain plan variables: `env=dev`, `release=` (empty),
`db_password=changeme` (Bamboo masks it by its name), `outcome=pass`.
Bamboo has no required or allowed-values notion; those live in bam's target
config, which the README supplies (§5).

## 3. Wiring

Child plans are declared on the parent with
`Plan.dependencies(new Dependencies().childPlans(...))`:

- `BAMLAB-TREE` → `BAMLAB-OK`, `BAMLAB-FLAKY`, `BAMLAB-SLOW`
- `BAMLAB-OK` → `BAMLAB-LEAF`

Bamboo triggers children only when the parent build succeeds, and refuses
a dependency on a plan that does not exist yet. `BamLabSpec.main`
therefore publishes leaves first: LEAF, OK, FLAKY, SLOW, ORPHAN, VARS,
FAIL, TREE. `BamLabSpec.plans()` returns them in that order and `main`
publishes the list, so the order is testable.

A full run of `BAMLAB-TREE` gives the tree bam must draw:

```
BAMLAB-TREE   ✓
├── BAMLAB-OK     ✓
│   └── BAMLAB-LEAF ✓
├── BAMLAB-FLAKY  ✗  (2 failed tests)
└── BAMLAB-SLOW   ✓  (running for ~3 min)
    [BAMLAB-ORPHAN  not run — only with the README's children: config]
```

## 4. `lab_step.py`

Standard library only, pure functions plus a `main`, like every other
script in the lab. Every job's script task is one line calling it, with
Bamboo variables passed as arguments.

| Flag | Effect |
| --- | --- |
| `--sleep N --tick S` | runs N seconds, printing `HH:MM:SS bamlab tick k/m` every S seconds and flushing each line |
| `--junit pass\|fail\|auto --junit-dir DIR` | writes `DIR/results.xml`: 3 test cases in one suite; in `fail` mode 2 fail, each with a message and a multi-line body; `auto` fails them exactly when the outcome fails |
| `--outcome pass\|fail\|first-attempt-fails` | the exit code; any failure prints `ERROR bamlab: deliberate failure (<plan>)` last |
| `--attempt-key KEY --state-dir DIR` | used by `first-attempt-fails`: KEY is `${bamboo.buildResultKey}` |
| `--show-vars` | prints every `bamboo_*` environment variable, sorted, as `name=value` |

**First attempt fails.** Restarting a build in Bamboo keeps its build
result key, so the key identifies the build across attempts. With
`first-attempt-fails`, the script looks for `DIR/KEY`: if it is absent,
this is the first attempt, so it creates the marker and fails; if it is
present, this is a restart, so it passes. The JUnit mode follows the
outcome (fail on the first attempt, pass on a restart). `DIR` defaults to
`~/.bamlab/attempts`. Only FLAKY's Test job uses this mode; its Build job
passes, so a Bamboo restart reruns just the Test job.

The marker lives on the agent, so a restart must land on the same agent
with the same home. `BAMLAB-FLAKY` requires `agent.role=host` (the host
agent's home persists; the containerized agent's may not). If the marker
is lost, a restart fails again; the README says so. Markers are tiny and
never cleaned up automatically; the README gives the one-line cleanup.

**Tests in Bamboo.** The JUnit parser (`lab-results/*.xml`) is a final
task on the jobs that write results (OK's Test job, FLAKY's Test job), so it
runs after a failing script task too. No other job has a parser, which
keeps bam's "no tests" path covered.

**Secrets.** `--show-vars` prints values as the agent received them.
Bamboo masks variables whose names contain `password` in build logs, which
is the behaviour bam's masking must agree with. No real secret is ever set
on a BAMLAB plan.

## 5. README for bam users

`lab/bamlab/README.md` holds:

- the plan table from §2;
- a `.bam.yaml` snippet: a `vars` target for `BAMLAB-VARS` with
  `required: [release]`, `options: {env: [dev, stage, prod]}` and a
  `${BAMLAB_RELEASE}` env reference; a `smoke` target for `BAMLAB-OK`; a set
  with OK and FAIL; and `children: {BAMLAB-TREE: [BAMLAB-OK, BAMLAB-FLAKY,
  BAMLAB-SLOW, BAMLAB-ORPHAN]}`;
- a checklist mapping each bam feature to the command to try, for example
  `bam run BAMLAB-TREE --watch` then `bam build tree` on the build key it printed,
  `bam build tree … --rerun-failed`, `bam build cancel` on SLOW,
  `bam run vars` with no `release`, `bam logs` on FAIL;
- the FLAKY marker caveat and its cleanup command.

forge-lab's root `README.md` and `CLAUDE.md` each gain one line pointing at
it, and `lab/README.md`'s tree lists `bamlab/`.

## 6. Testing

- **pytest** (`test_lab_step.py`): tick count and line format with a fake
  sleep; JUnit XML in both modes parses and has the right failure count;
  each outcome maps to its exit code; the first-attempt marker fails, then
  passes, for the same key, and is independent per key; `--show-vars` prints
  only `bamboo_*` variables, sorted.
- **Java** (`BamLabSpecTest`): every plan validates through
  `EntityPropertiesBuilders.build`, as the other spec tests do; `plans()` is
  in publish order with every child before its parent; TREE's and OK's
  children are exactly those in §3; only FLAKY has an agent requirement; the
  JUnit parser appears only on OK's and FLAKY's Test jobs, as a final task.
- `make lint` runs both. The first real check is a manual one after
  publishing: run `BAMLAB-TREE`, see the tree of §3 in Bamboo, restart
  FLAKY's build and see it pass.

## 7. Follow-up: spec #2 in bam-cli

After this lab is published and has run once, bam-cli gets its own spec:
e2e tests for tree, rerun-to-green, cancel, logs and variables against these
plans, and recorder targets that refresh fixtures from them. bam-cli is a
public repository, so its tests take the plan keys from flags or config, and
its recorder renames the project to the `LAB` placeholder as it does today.
