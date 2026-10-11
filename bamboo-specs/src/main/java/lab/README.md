# lab/ — one directory per Bamboo plan

Each directory here is one plan: its spec (Java) and every piece of code that
plan executes (Python, Terraform, Ansible), side by side. Nothing about a plan
lives anywhere else.

```
lab/
├── shared/                     # used by 2+ plans
│   ├── SpecConstants.java      # BAMBOO_URL, REPO_NAME
│   ├── python/forgelab/        # the lab's one Python library (stdlib only)
│   ├── terraform/              # VM lifecycle (modules/multipass = backend seam)
│   └── ansible/                # site.yml + roles, generated inventory (gitignored)
│       └── roles/{common,dcos,k8s,hdfs,keycloak,opensearch}
├── provisioncluster/           # FORGE-PROV
│   ├── ProvisionClusterSpec.java
│   └── scripts/{provision.py,install.py,verify.py}
├── deprovisioncluster/         # FORGE-DEPROV
│   ├── DeprovisionClusterSpec.java
│   └── scripts/deprovision.py
├── agentimage/                 # AGENT-BUILD
│   ├── BuildAgentImageSpec.java
│   └── README.md               # build script lives in the bamboo-agent repo
├── specspublish/               # FORGE-SPECS
│   ├── PublishSpecsSpec.java
│   └── scripts/publish_specs.py
└── bamlab/                     # BAMLAB-* (plans for trying bam)
    ├── BamLabSpec.java
    └── scripts/lab_step.py
```

Yes, Python and Terraform live inside a Maven source root. That is deliberate:
one lookup gets you everything a plan runs. Maven compiles `.java` and ignores
the rest, so nothing here affects the build.

A cluster's type, its node sizing, and which technologies it runs live in
`cluster_configs/<name>_cluster.yaml` at the repo root — not under `lab/`,
because it is committed input, unlike the generated `cluster_registered/`.
Every plan's checkout carries it; `forgelab/clusterconfig.py` is the only
code that reads it.

## The forgelab package

`shared/python/forgelab/` is the only library in the lab, standard library only:

| module         | responsibility                                            |
| -------------- | --------------------------------------------------------- |
| `proc.py`      | `LabError`/`die`, `run`, `run_out`, `require_tools`, `main` |
| `paths.py`     | every path, derived from the package's own location        |
| `clusterconfig.py` | parse and validate `cluster_configs/<name>_cluster.yaml`; derive the Terraform nodes map, inventory groups, and registry sizing |
| `planvars.py`  | resolve `(cluster_name, cluster_config)` into a cluster name and a loaded `ClusterConfig` |
| `terraform.py` | init / workspace / apply-with-retry / destroy              |
| `multipass.py` | the VM backend seam — parse `multipass list`, purge VMs    |
| `inventory.py` | render the ansible inventory, read hosts back out          |
| `sshconf.py`   | per-cluster `~/.forgelab/ssh_config.d/<cluster>.conf`      |
| `registry.py`  | `cluster_registered/<cluster>_cluster_info.yml`            |
| `credentials.py` | `~/.forgelab/<cluster>-credentials.yml`, per-cluster secrets |

Parsing and rendering are pure functions taking and returning strings; every
external command goes through `proc.run`. That split is what makes the tests
cheap — see `src/test/python/`.

`infra/` may import `forgelab` and nothing else under `lab/`. The dependency
direction is one-way: `infra/ → lab/shared/`, never the reverse, and never
plan → plan.

## Adding a plan

1. Create `lab/<planid>/` — lowercase, no dashes (it is a Java package name).
   One directory per plan spec.
2. Write the spec as `lab/<planid>/<Name>Spec.java`, package `lab.<planid>`.
   Add one test at `src/test/java/lab/<planid>/<Name>SpecTest.java` calling
   `EntityPropertiesBuilders.build(...)` — that is the offline validation
   `make lint` runs.
3. Put the code it executes in `lab/<planid>/scripts/`. No other plan
   references it.
4. Reused by 2+ plans? It goes in `lab/shared/python/forgelab/`. A plan never
   reaches into another plan's directory.
5. Not executed by a plan — helm values, license fetch, host-agent install/run
   — it belongs in `infra/`, not here.
6. Nothing to register. `specspublish/scripts/publish_specs.py` discovers
   `lab/*/*Spec.java` and derives the class from the path, so the directory
   layout is the plan list. Your spec needs a `main()` that calls
   `BambooServer.publish` — that is what the publisher invokes.

## Conventions

- Entrypoints take their inputs as positional arguments and run from any
  working directory. Put `shared/python` on `sys.path` by relative path
  (`Path(__file__).resolve().parents[2] / "shared" / "python"`) and derive every
  other path from `forgelab.paths` — Bamboo runs these from its own build
  directory, never from the repo root.
- Entrypoint filenames use underscores, not dashes: the tests import them to
  stub out their externals.
- Wrap `main(argv)` in `proc.main` so a `die` or a failed command prints one
  line instead of a traceback.
- A spec's `ScriptTask` body stays a one-liner calling an entrypoint in this
  tree. The logic lives in Python so it runs identically from a plain terminal
  (`make provision`) and from CI — the CI-agnostic core.
- Script task paths are repo-relative from Bamboo's checkout root, so they read
  `bamboo-specs/src/main/java/lab/<planid>/scripts/<script>.py`.
- Standard library only. The host agent has no venv; whatever an entrypoint
  imports must ship with python3.
- An ansible role that installs something ends its `tasks/main.yml` by
  appending `{'name': ..., 'version': ...}` to the `forgelab_components` fact.
  `site.yml`'s last play writes the collected list to `component_report`, and
  `provision.py` renders it into the cluster's info file. That is the only way
  a component reaches `cluster_registered/`; no Python knows the list.
- Tests live in `src/test/python/`, run by `make lint`.
