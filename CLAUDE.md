# forge-lab

Personal CI/CD lab: Bamboo on Rancher Desktop K8s + pipelines that provision
Multipass VM clusters (k8s or dcos). Design: docs/superpowers/specs/2026-07-23-forge-lab-design.md.
For how to actually use a provisioned cluster's technologies (Keycloak, HDFS,
OpenSearch/Dashboards) and k9s day-to-day, see docs/using-cluster-addons.md.
For running the Provision plan itself — plan variable values, expected VM
counts per config, and the per-technology health checks — see
docs/provision-usage.md.

## Commands

- `make bootstrap` — one-shot first-run: namespace, secrets, Postgres + Bamboo,
  and Bamboo's UNATTENDED setup (no wizard). Skips the manual license/admin
  steps and starts the remote-agent JMS broker automatically. Login `admin/admin`
- `make up` / `make down` — CI stack (Bamboo + Postgres) on namespace `ci`
  (`up` needs the secrets from `make bamboo-secrets`; `down` keeps PVCs)
- `make reset` — DESTRUCTIVE: wipe Bamboo PVCs + DB and reinstall; unattended
  setup re-runs on the fresh DB (re-licensed, broker up — no wizard). Use when
  boot fails with "Shared configuration ... does not exist" (stale DB vs empty
  shared-home)
- `make bamboo-secrets` — create/refresh the unattended-setup secrets: license
  (24h timebomb, runtime-only, never committed), sysadmin `admin/admin`, and the
  40-hex agent security token (created once, shared server<->agent)
- `make ui` — port-forward Bamboo to http://localhost:8085
- `make bamboo` — the one command for a local plan run, replacing `make up`
  + `make ui` + `make agent-run` in three terminals: it runs `up`, waits for
  the rollout, forwards 8085/54663 in the background, installs the agent if
  its jar is missing, then runs the agent in the foreground. Ctrl-C stops the
  agent and takes the forward down with it, so no orphan holds 8085. A
  port-forward already running elsewhere is reused; a half-open pair (one of
  the two ports taken) is an error naming the port rather than a later
  "address already in use"
- `make bamboo-restart` — the fix for "license has expired ... read-only mode".
  The timebomb re-arms on server start, so a pod restart buys another 24h;
  PVCs and DB are untouched. `make relicense` does NOT help here: Atlassian
  rarely rotates the published key, and re-entering the string already in
  `bamboo.cfg.xml` fails with "The license key you entered was invalid."
- `make license` / `make relicense` — fetch the 24h timebomb key to clipboard
  (only needed for manual wizard / a genuinely rotated key; `bootstrap` handles
  it via secret)
- `make agent-install` / `make agent-run` — host-local Bamboo agent. Token is
  auto-read from the `bamboo-agent-token` secret; approve the agent once in
  Administration > Agents. Unattended setup already started the broker (54663).
  `agent-run` seeds capability `agent.role=host`; the Provision/Deprovision
  plans *require* it so they never schedule on the containerized k8s agent
  (`agent.role=ci`), which has no terraform/multipass. It also points the
  cluster registry at `<this clone>/cluster_registered`, via both
  `FORGELAB_REGISTRY_DIR` and `~/.forgelab/registry_dir`
- `make provision CLUSTER=lab1 [CONFIG=lab1]`
  / `make deprovision CLUSTER=lab1` — provision also writes
  `~/.forgelab/ssh_config.d/<cluster>.conf` (included from `~/.ssh/config`) so
  `ssh lab1-management-1` / `ssh <node-ip>` work as `ubuntu` with the lab key;
  deprovision removes it. `CONFIG=` picks which
  `cluster_configs/<name>_cluster.yaml` to build from; empty means the config
  named after the cluster. There is no fallback — a missing config is an error
  naming the path. Provision also writes
  `cluster_registered/<cluster>_cluster_info.yml` (tracked, uncommitted) as its
  last step; deprovision deletes it
- `make addons CLUSTER=lab1 [CONFIG=lab1]` — re-run the install stage only,
  against an existing cluster's inventory. Use it to iterate on an ansible role
  without a full rebuild
- `make lint` — pytest + terraform fmt/validate + ansible-lint + mvn test
  (pytest is a host tool like shellcheck was: `uv tool install pytest`)
- `make hooks-install` — one-time: `git config core.hooksPath infra/githooks`,
  so a push to `main` republishes every plan. The hook skips quietly when
  Bamboo is unreachable and never blocks a push
- `make specs-publish` — publish every plan now. Needs `make ui` running and
  a Bamboo PAT in `~/.forgelab/bamboo_pat` (chmod 600); `FORGELAB_BAMBOO_PAT`
  overrides it for one run. `bamboo-specs/.credentials` is generated from it,
  never hand-maintained

## Layout map

Plan root is `bamboo-specs/src/main/java/lab/` (`$(LAB)` in the Makefile) —
one directory per Bamboo plan, holding its spec AND the code it runs:

- `lab/<planid>/` — `<Name>Spec.java` + `scripts/` for that plan alone;
  the scripts are the CI-agnostic core, called by both the spec and the Makefile
- `lab/shared/` — used by 2+ plans: `SpecConstants.java`,
  `python/forgelab/` (the lab's one library, stdlib only; `credentials.py` writes
  `~/.forgelab/<cluster>-credentials.yml`, referenced by the registry, never inlined),
  `terraform/` (`modules/multipass/` = swappable VM backend boundary), `ansible/`
- `lab/bamlab/` — `BAMLAB-*`: synthetic plans with fixed outcomes (a parent
  with child plans, a first-attempt-fails child, a slow one, a failing one) for
  trying bam against this Bamboo. One spec class publishes all eight, leaves
  first; every job runs `scripts/lab_step.py`. See its README
- `infra/` — lab operations NOT run by any plan: `helm/` chart values,
  `agent/` host-agent install+run, `scripts/` license fetch
- `cluster_configs/` — committed input, one `<name>_cluster.yaml` per cluster:
  its type, its node sizing, and which technologies it runs. Parsed and
  validated by `forgelab/clusterconfig.py`, which expands it into the Terraform
  `nodes` map, the ansible inventory groups, and the registry's sizing — so
  those three can no longer disagree. Repo-relative, unlike
  `cluster_registered/`: it is committed, so every Bamboo checkout carries it
- `cluster_registered/` — generated output, tracked, owned by PROV/DEPROV:
  `<cluster>_cluster_info.yml` (IPs, sizing, ssh hints, installed components)
  written by provision after verify passes, deleted by deprovision. The
  pipeline never commits it — you do. Location resolves
  `$FORGELAB_REGISTRY_DIR` → `~/.forgelab/registry_dir` (one line, seeded by
  `agent-run`) → this checkout. Bamboo gives each plan its own throwaway
  checkout, so a repo-relative path strands PROV's file where DEPROV can never
  delete it; the pointer file is read per run, so a long-running agent picks up
  a change without a restart
- Python and Terraform inside a Maven source root is deliberate — one lookup
  per plan. Maven compiles `.java` and ignores the rest.
- `infra/` may import `forgelab` and nothing else under `lab/`; the dependency
  direction is one-way and no plan reaches into another plan's directory
- Python tests live in `bamboo-specs/src/test/python/`

## Adding a plan

Full contract in `bamboo-specs/src/main/java/lab/README.md`. In short:

1. `lab/<planid>/` — lowercase, no dashes (it is a Java package name)
2. Spec `lab/<planid>/<Name>Spec.java` + one offline-validation test at
   `src/test/java/lab/<planid>/<Name>SpecTest.java`
3. Its scripts in `lab/<planid>/scripts/`; nothing outside that plan uses them
4. Shared by 2+ plans → `lab/shared/`; never reach into another plan's dir
5. Not plan-executed → `infra/`, not `lab/`
6. Nothing to register — the publisher discovers `lab/*/*Spec.java` and
   derives the class from the path; your spec needs a `main()` calling
   `BambooServer.publish`
7. `ScriptTask` bodies are repo-relative from Bamboo's checkout root:
   `bamboo-specs/src/main/java/lab/<planid>/scripts/<script>.py`

## Conventions

- Commits: Roj's git identity ONLY — no Claude co-author/footers
- Scripts are Python 3, standard library only (the host agent has no venv).
  Entrypoint filenames use underscores so tests can import them; wrap
  `main(argv)` in `proc.main`; parsing/rendering stays pure, shelling out goes
  through `proc.run`
- Never commit: license keys, generated inventories, tfstate
- Multipass units: "4G"/"20G", not Gi
- Terraform applies run `-parallelism=1` (`terraform.apply_retry`): concurrent
  `multipass launch` races give every VM in the batch the same MAC, hence one
  shared DHCP lease. Do not drop the flag
- Cluster technologies are opt-in per cluster: `technologies.<name>.enabled` in
  `cluster_configs/<cluster>_cluster.yaml`. A disabled technology keeps its
  sizing in the file, unvalidated, and contributes no VMs, no inventory groups
  and no ansible roles — enablement and sizing cannot disagree because they are
  the same block. k9s is not a technology — it ships with the k8s role. Secrets
  live in `~/.forgelab/<cluster>-credentials.yml` (0600), never in
  `cluster_registered/`
- A VM's role is its name: `<cluster>-<role>-<n>`. For `cluster_nodes` the role
  is the key verbatim (`management`, `compute`); for a technology's nodes it is
  `<technology>-<node>` (`hdfs-namenode`, `opensearch-master`), so two
  technologies can each own a `master`. The ansible group is the role with `-`
  replaced by `_`, because ansible warns on dashed group names. Rename a key in
  the config and the VM name, the group and the registry's `role` field all move
  together. hdfs owns two node roles — `namenode` (metadata only, no DataNode)
  and `datanode` — and `clusterconfig` requires exactly one NameNode, since
  non-HA HDFS has exactly one
- splunk owns three node roles — `cluster-manager`, `indexer`, `search-head` —
  with counts fixed by the product: one manager, one search head, two indexers
  minimum (`replication_factor = 2`). Splunk Enterprise has NO arm64 build, so
  those VMs run the amd64 build under `qemu-user` translation: ~2 min service
  start, ~5x native search. The Universal Forwarder on every other node is
  native arm64. Both roles pin `SPLUNK_OS_USER` in `splunk-launch.conf` before
  the first start — Splunk otherwise records whoever ran that start and then
  setuids to a user that cannot read its own `SPLUNK_HOME`. The forwarder runs
  as root because kubelet keeps `/var/log/pods` root-only. The Developer
  licence lives at `~/.forgelab/splunk-dev-license.xml`, never in the repo;
  without it instances fall back to a trial that expires into Splunk Free,
  which forbids distributed search
- PROV takes two plan variables: `cluster_name` and `cluster_config` (empty
  means the config named after the cluster). Nothing overrides the config —
  it is the single source of truth, validated in `forgelab/clusterconfig.py`
  and nowhere else
- PROV and DEPROV each open with a `Validate` stage carrying NO `agent.role`
  requirement, so a bad cluster name or config fails in seconds on any agent
  instead of queueing behind the host agent. Entrypoints call `planvars` first
  as well, so `make provision` fails identically
- Plans are published two ways and both call
  `lab/specspublish/scripts/publish_specs.py`: the `pre-push` hook on `main`,
  and the FORGE-SPECS plan polling `main` every 3 minutes. There is no list of
  spec classes anywhere — the publisher globs `lab/*/*Spec.java`, so adding a
  plan directory is the whole registration step
- Push through `git push no-mistakes <branch>`, never `origin`: the
  `.no-mistakes.yaml` gate runs `make lint` on the Mac, using host pytest (uv),
  terraform (Homebrew), ansible-lint (Nix) and mvn/java (sdkman). Use `gh-axi`
  for GitHub, not raw `gh`. Run `/code-review` only on PRs that did not go
  through no-mistakes
