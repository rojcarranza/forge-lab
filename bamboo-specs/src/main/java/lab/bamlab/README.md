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

Put this in the `.bam.yaml` of any directory you drive bam from. Set
`default_server` to the alias you gave this Bamboo in `bam server add`;
without it bam falls back to your machine's default server, which may be
another Bamboo:

```yaml
version: 1
default_server: lab   # the alias of this Bamboo in `bam server add`
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
is gone (another agent, a wiped home), the restart fails again. A marker
older than 24 hours no longer counts, so a plan recreated or a Bamboo
reinstalled (build numbers start again at 1) still fails its first attempts.
Clear old markers with:

```bash
rm -rf ~/.bamlab/attempts
```
