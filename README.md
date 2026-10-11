# forge-lab

A personal, fully code-defined CI/CD infrastructure lab: a Bamboo server and
agent running on a Mac, driving pipelines that provision, install, and
deprovision named multi-node VM clusters (Kubernetes or DC/OS) for deliberate
DevOps practice.

Design source of truth: [`docs/superpowers/specs/2026-07-23-forge-lab-design.md`](docs/superpowers/specs/2026-07-23-forge-lab-design.md).

First run on a new machine (macOS or Ubuntu): [`SETUP.md`](SETUP.md).

Day-to-day guides: [`docs/provision-usage.md`](docs/provision-usage.md) for
running the Provision plan and checking what installed;
[`docs/provisioning-without-bamboo.md`](docs/provisioning-without-bamboo.md)
for the same lifecycle driven by `make` alone, with no Bamboo server or agent;
[`docs/using-cluster-addons.md`](docs/using-cluster-addons.md) for using
Keycloak, HDFS, OpenSearch and k9s once a cluster is up.

## What is this

Everything in this repo exists to answer one question repeatedly, on demand:
*"spin me up a fresh k8s or dcos cluster, let me practice on it, then tear it
down cleanly"* — driven entirely from CI pipelines defined as code, not
clicked together by hand.

- **CI engine:** Bamboo Data Center, running in the local Rancher Desktop
  Kubernetes cluster, licensed with a free 24h "timebomb" testing key
  (fetched via `make license`, re-applied each session via `make relicense`).
- **Pipelines as code:** Bamboo Specs (Java + Maven) define the plans;
  `mvn test` validates them offline before publish.
- **CI-agnostic core:** all real provisioning logic lives in shell scripts
  that sit beside the plan spec that runs them (`lab/<planid>/scripts/`).
  Bamboo Specs tasks, the Makefile, and (if Bamboo's DC line is ever pulled)
  a future Jenkinsfile all call the *same* scripts. This isolates the Bamboo
  licensing risk from the actual lab work.
- **Provisioned target:** Multipass VMs, wired up with Terraform (VM
  creation) + Ansible (OS-level install: kubeadm/containerd for k8s, or the
  DC/OS installer).
- **Opt-in technologies:** Keycloak, HDFS, OpenSearch/Dashboards and a
  distributed Splunk deployment, each enabled per cluster in its config file.
  See "Splunk: emulated Enterprise, native forwarders" for the one that runs
  emulated, and [`docs/using-cluster-addons.md`](docs/using-cluster-addons.md)
  for using them day to day.

This is a personal learning lab, not production infrastructure. Design
choices favor simplicity, local-only operation, and clear boundaries over
robustness at scale.

## Architecture

```
Mac host (32Gi+)
├── Rancher Desktop K8s
│   ├── ns ci: bamboo-server   (Atlassian Helm chart, timebomb 24h license)
│   └── ns ci: postgres        (Bitnami chart, PVC)
├── bamboo-agent               (plain process on Mac — Phase 1)
│   └── has: JDK 21, terraform, ansible, multipass CLI (direct)
└── Multipass VMs              (created by pipelines only)
    ├── <name>-management-1..N
    ├── <name>-compute-1..N
    ├── <name>-hdfs-namenode-1
    ├── <name>-hdfs-datanode-1..N
    ├── <name>-opensearch-master-1..N
    ├── <name>-splunk-cluster-manager-1  (amd64 Splunk under qemu-user)
    ├── <name>-splunk-indexer-1..N       (amd64 Splunk under qemu-user)
    └── <name>-splunk-search-head-1      (counts/sizes from cluster_configs/<name>_cluster.yaml)
```

**CI-agnostic core:** all real logic lives in the plans' shell scripts
(calling Terraform + Ansible) and runs from a plain shell. Bamboo Specs tasks
are one-liners calling those scripts; the Makefile calls the same scripts; a
future Jenkinsfile would too. This isolates the Bamboo licensing risk.

**Swappable VM backend:** the root Terraform module exposes a stable
contract (cluster_name + node specs in → node IPs + rendered inventory out)
and delegates to `lab/shared/terraform/modules/multipass/`. Swapping to
UTM or libvirt later means writing a sibling module and flipping a `backend`
variable. Ansible only ever sees the generated inventory, never the backend.
Deprovision's backend sweep step is backend-scoped too.

### Key decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | CI engine | **Bamboo DC + 24h timebomb license** | Atlassian stopped new DC trial licenses 2026-03-30, but free 10-user 24h timebomb keys remain published on the Atlassian developer site for testing. Re-apply key per session; `bamboo-home` PVC persists everything else. Jenkins remains a paper fallback via the CI-agnostic core. |
| D2 | Host K8s | **Rancher Desktop** | Already installed, lighter than UTM kubeadm, easy localhost access. |
| D3 | Agent placement (Phase 1) | **Plain process on Mac host** | Direct access to `multipass`/`terraform`; zero SSH plumbing. Pod agent + SSH-to-host deferred to Phase 2+. |
| D4 | Per-cluster config | **YAML config per cluster** (`cluster_configs/<name>_cluster.yaml`) | Node counts, cpu/mem/disk, cluster type and enabled technologies versioned in repo, in one file; no fallback file and no field defaulting, so the file is the whole truth; hand-written parser keeps the toolchain stdlib-only. |
| D5 | TF state | Local backend + workspace per cluster | No cloud dependency; no state collisions. |
| D6 | K8s install method | kubeadm + containerd | Matches real-world and prior UTM learning. |
| D7 | VM backend | **Multipass, but swappable** | Backend isolated in a Terraform module; UTM/libvirt later = new module + one variable flip. Ansible only ever sees the generated inventory. |

## Prerequisites

Install on the Mac host before starting:

- **Rancher Desktop** — provides the local Kubernetes cluster Bamboo runs
  on, plus a working `kubectl`/`helm` context.
- **helm** (v3) — installs the Bamboo + Postgres charts.
- **terraform** — provisions Multipass VMs.
- **ansible** — installs k8s/dcos onto provisioned VMs.
- **multipass** — creates the local VMs pipelines target.
- **jq** — used by scripts to parse JSON (Terraform output, `multipass list --format json`, etc).
- **JDK 21 + maven** — builds and validates Bamboo Specs (`bamboo-specs/`),
  and runs the Bamboo 12.1.8 agent installer, which needs 21.
- **shellcheck** and **ansible-lint** — static checks, both part of `make lint`.

Quick check that everything is on `PATH`:

```bash
for bin in kubectl helm terraform ansible ansible-lint multipass jq mvn shellcheck; do
  command -v "$bin" >/dev/null || echo "MISSING: $bin"
done
java -version   # expect 21.x
```

Rancher Desktop must be **running** (with Kubernetes enabled) before `make up`.

**ansible-galaxy collections (one-time, needed for `make lint`)** — if
`ansible-lint` was installed via Homebrew, it runs in its own isolated venv
and does *not* see the collections bundled inside Homebrew's `ansible`
formula, even though `ansible-playbook` finds them fine. This surfaces as
`unknown-module: community.general.modprobe` (or `ansible.posix.sysctl`)
from `ansible-lint` alone. Fix once, for both tools:

```bash
ansible-galaxy collection install community.general ansible.posix
```

This installs into `~/.ansible/collections/`, the shared default path both
Homebrew `ansible-lint` and `ansible-playbook` search.

## Quick start

Target: working lab, from a clean checkout, in under an hour.

```bash
# 1. One-time setup checks/bootstrap (verifies prerequisites, prepares local state dirs)
make setup

# 2. Bring up the CI stack — Bamboo + Postgres into the `ci` namespace on
#    Rancher Desktop's Kubernetes
make up

# 3. Grab the 24h timebomb license key (copied to your clipboard), then open
#    the UI and paste it into the setup wizard (see "License ritual" below)
make license
make ui
# → http://localhost:8085
```

Then, once Bamboo is licensed and reachable:

```bash
# 4. Get an agent token, then install and start the host-local Bamboo agent
#    Bamboo UI → Administration → Agents → "Install remote agent" shows the
#    token (enable "security token verification" there first if prompted);
#    export it before running install_agent.py:
export AGENT_TOKEN=<token from the UI>
make agent-install
make agent-run
```

`install_agent.py` copies the installer jar straight out of the Bamboo
server pod with `kubectl cp` (the jar isn't downloadable without an admin
login, but it's right there in the pod), then runs it against
`/agentServer/` with your token. Overridable via `BAMBOO_NAMESPACE`
(default `ci`) and `BAMBOO_CONTAINER` (default `bamboo`); needs `kubectl`
(Rancher Desktop provides it) and a JDK on `PATH`.

Leave `make agent-run` running in its own terminal (or under `launchd` —
see `infra/agent/run_agent.py`); it needs to stay up for plans to build.

`run_agent.py` also seeds the capability `agent.role=host` into
`<agent-home>/bin/bamboo-capabilities.properties`. The Provision/Deprovision
plans declare a matching **requirement**, which is what keeps the multipass
toolchain jobs off the containerized k8s agent (`agent.role=ci`, no terraform
or multipass in that image). Bamboo only reads that properties file on an
agent's *first* startup, so for an agent that is already registered add the
capability once against the running server:

```bash
TOKEN=$(grep -m1 '^token=' bamboo-specs/.credentials | cut -d= -f2-)
AGENT_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8085/rest/api/latest/agent | jq -r '.[0].id')
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"key":"agent.role","value":"host"}' \
  "http://localhost:8085/rest/api/latest/agent/$AGENT_ID/capability"   # 204 = added
```

(Or by hand: Administration → Agents → *your host agent* → Capabilities → Add
capability, type **Custom**, key `agent.role`, value `host`.)

With security token verification enabled, a freshly started agent registers
but stays **pending until you approve it**: Administration → Agents →
Agent authentication → approve the new UUID (the agent log prints the exact
approval URL). Until then the agent retries every 60 seconds.

Before publishing Specs, two one-time Bamboo UI steps are required (both
persist in the `bamboo-home` PVC, so this is genuinely one-time per
install):

1. **Specs credentials** — `bamboo-specs/.credentials` is gitignored and
   not created for you. Bamboo Specs authenticates via a personal access
   token; create one in the Bamboo UI (profile → Personal Access Tokens),
   then write it as a Java properties file:
   ```bash
   echo "token=<your PAT>" > bamboo-specs/.credentials
   ```
   (`exec:java`'s working directory is the `bamboo-specs/` module root, so
   the file must live there, not at the repo root.)
2. **Linked repository** — Administration → Linked Repositories → add
   `git@github.com:r0jjames/forge-lab.git`. Both forge-lab specs
   (`ProvisionClusterSpec`, `DeprovisionClusterSpec`) call
   `defaultRepository()`, which resolves against this linked repo by name —
   publish fails without it. `BuildAgentImageSpec` needs no such step: it
   declares a plan-local repository for the public bamboo-agent repo.

```bash
# 5. Publish all Bamboo Specs plans, proving the Specs → server publish
#    loop works end to end
make specs-publish
```

This also publishes `AGENT-BUILD`, the plan that builds the containerized CI
agent image from the [bamboo-agent](https://github.com/r0jjames/bamboo-agent)
repo. It only runs on an agent with the `agent.role=ci` capability (that same
containerized agent), so it stays queued until one is deployed and approved.

At this point you have a licensed Bamboo server, a connected local agent, and
published plans — the CI up (Phase 1) definition of done. Provisioning a
cluster (Phase 2+) is covered next.

## Cluster configs

A cluster is described by one file, `cluster_configs/<name>_cluster.yaml`:

```yaml
cluster:
  type: k8s                 # k8s | dcos

cluster_nodes:
  management:               # required — the control node
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G
  compute:
    count: 2
    cpu: 2
    memory: 3G
    disk: 20G

technologies:
  hdfs:
    enabled: true
    nodes:
      namenode:             # exactly one — HDFS here is non-HA
        count: 1
        cpu: 2
        memory: 4G
        disk: 20G
      datanode:
        count: 3
        cpu: 2
        memory: 4G
        disk: 40G
  opensearch:
    enabled: false          # sizing below is kept, unvalidated, and builds nothing
    nodes:
      master:
        count: 3
        cpu: 2
        memory: 6G
        disk: 40G
  keycloak:
    enabled: true           # runs as pods on the cluster; declares no nodes
  splunk:
    enabled: true
    nodes:
      cluster-manager:      # exactly one — and it is the licence manager too
        count: 1
        cpu: 2
        memory: 4G
        disk: 20G
      indexer:              # at least two — replication_factor = 2
        count: 2
        cpu: 4
        memory: 6G
        disk: 60G
      search-head:          # exactly one — a search head *cluster* needs three
        count: 1
        cpu: 4
        memory: 6G
        disk: 30G
```

Every value is required when its block is enabled — there is no fallback file
and no defaulting, so reading this one file tells you exactly what you get.
`memory` and `disk` are multipass units (`4G`, `512M`), never `Gi`.

Build it with `make provision CLUSTER=lab1`, or point a differently-named
cluster at it with `make provision CLUSTER=lab2 CONFIG=lab1`.

## Pipeline usage

### Provisioning a cluster

```bash
make provision CLUSTER=lab1              # builds from cluster_configs/lab1_cluster.yaml
make provision CLUSTER=lab2 CONFIG=lab1  # builds lab2 from lab1's config
```

What happens, stage by stage (whether triggered via `make` or via the
Bamboo `ProvisionClusterPlan`, since both call the same
`lab/<planid>/scripts/*.py`):

1. **Validate** — checks `cluster_name` matches `[a-z0-9-]+`; refuses if
   `multipass list` already shows `<name>-` prefixed VMs; loads and validates
   `cluster_configs/<config>_cluster.yaml` (`<config>` defaults to
   `cluster_name`). There is no fallback file — a missing config is an error
   naming the path it looked for.
2. **Provision** — `terraform workspace select/new <name>`, then
   `apply -var-file=<generated .tfvars.json> -input=false` (serially — see the
   Multipass MAC note under Troubleshooting); renders
   `lab/shared/ansible/inventory/<name>.ini` from the Terraform output,
   refusing to continue if two nodes came back with the same IP. Also writes
   `~/.forgelab/ssh_config.d/<name>.conf` so `ssh <name>-management-1` and
   `ssh <node-ip>` log in as `ubuntu` with `~/.forgelab/id_ed25519` — no flags
   needed. The first provision prepends a single
   `Include ~/.forgelab/ssh_config.d/*.conf` line to `~/.ssh/config`
   (backing the old file up as `~/.ssh/config.forgelab.bak`); the include has
   to sit above any `Host *` block because ssh keeps the first value it finds
   for an option. `make deprovision` deletes the per-cluster file.
3. **Install** — `ansible-playbook site.yml -i inventory/<name>.ini -e cluster_type=<type> -e addons=<enabled technologies>`.
   k8s path: kubeadm + containerd + CNI. dcos path: pinned installer version,
   cached locally.
4. **Verify** — k8s: all nodes reach `Ready`. dcos: UI health endpoint
   responds OK. The pipeline fails if verification doesn't pass.
5. **Register** — writes `cluster_registered/<name>_cluster_info.yml` (see
   below). Last on purpose: a cluster that failed verification never gets an
   entry.

### Cluster info files (`cluster_registered/`)

A successful provision leaves one YAML file per live cluster — where to ssh,
how the nodes are sized, and what got installed:

```yaml
# cluster_registered/lab1_cluster_info.yml
cluster: lab1
type: k8s
provisioned_at: "2026-08-03T12:20:25Z"
ssh:
  user: ubuntu
  key: "~/.forgelab/id_ed25519"
  example: ssh lab1-management-1
nodes:
  - name: lab1-management-1
    role: management
    ip: 192.168.252.10
    cpu: "2"
    mem: 4G
    disk: 20G
  # ... one entry per compute node
components:
  - name: kubernetes
    version: "1.30"
  - name: containerd
  - name: flannel
    version: latest
```

`make deprovision` deletes the file, so the directory listing is the list of
clusters that exist. The files are tracked but **nothing commits them for you**
— a provision or teardown shows up in `git status` and you commit it if you
want that cluster in history. The PROV plan additionally publishes the file as
the `cluster-info` build artifact.

The components list comes from Ansible, not from Python: each role appends to a
`forgelab_components` fact and the last play in `site.yml` hands the collected
list back to `provision.py`. A new role shows up in the file as soon as it
declares itself — nothing else to change.

**Where the files land.** Resolved per run, in order:

1. `$FORGELAB_REGISTRY_DIR`
2. `~/.forgelab/registry_dir` — one line, written by `make agent-run`
3. `<this checkout>/cluster_registered` — what a hand-run `make provision` uses

The indirection exists because Bamboo gives every plan its own working copy
under `~/.forgelab/bamboo-agent-home/xml-data/build-dir/FORGE-<PLAN>-JOB1`.
Left repo-relative, PROV would write into a throwaway directory and DEPROV —
a *different* throwaway directory — could never clean it up. The pointer file
is read on every run, so a long-lived agent picks up a change without a
restart.

### DC/OS: installer cache and the Apple Silicon blocker

DC/OS installs use a pinned installer version, downloaded once and cached
at `~/.forgelab/cache/dcos_generate_config.sh` (`~967MB`; the role's
`dcos_installer_local` default). Fetch it manually before a first dcos run,
or just let the role's bootstrap task fetch/cache it on demand:

```bash
mkdir -p ~/.forgelab/cache
curl -fSL -o ~/.forgelab/cache/dcos_generate_config.sh \
  https://downloads.dcos.io/dcos/stable/2.0.3/dcos_generate_config.sh
```

The role is pinned to **2.0.3**, not the newer 2.2.13 line — `2.2.13`
returns `403 Forbidden` from `downloads.dcos.io` (dead link), while `2.0.3`
is confirmed available and downloads cleanly.

**Known blocker — DC/OS does not run on Apple Silicon (arm64) hosts.** The
DC/OS 2.x `genconf` step runs an amd64-only Docker image; on an arm64 host
(any M-series Mac) it fails deterministically during install:

```
exec /installer_internal_wrapper: exec format error
```

This is an architecture mismatch, not a flaky failure — retrying reproduces
the identical error every time. The `dcos` Ansible role itself is
static-complete and lint-clean; a live `dcos` install requires genuine
amd64 hardware (or an amd64 VM/emulation layer outside this lab's scope).
`k8s` is unaffected and is the recommended path on Apple Silicon hosts.

### Splunk: emulated Enterprise, native forwarders

`cluster_configs/splunk1_cluster.yaml` builds a real distributed Splunk
deployment — one cluster manager, two indexers, one search head — with a
Universal Forwarder on every other node in the cluster:

```bash
make provision CLUSTER=splunk1     # 11 VMs, 48G RAM, ~45 min
```

**Splunk Enterprise has no arm64 build.** Splunk's own system requirements
say so: *"The ARM architecture is not supported for use with Splunk
Enterprise at this time."* Unlike the DC/OS blocker above, this one is
worked around rather than fatal: the Enterprise VMs run the **amd64** build
under `qemu-user` translation, which the `splunk` role sets up
(`qemu-user-static` + binfmt, plus amd64 multiarch libc from
`archive.ubuntu.com`, since the arm64 image's `ports.ubuntu.com` carries no
amd64). Measured cost on a 4-cpu VM:

| | Emulated |
| --- | --- |
| Service start | ~2 min (hence `TimeoutStartSec=600`) |
| Search over 200k events | ~29 s, roughly 5x native |
| Web UI response | unaffected — 8 ms |

The Universal Forwarder *does* ship arm64, so the forwarders are native and
pay none of this. Enterprise and the forwarder are pinned to one version and
one build hash (`10.4.2` / `33c3bf42cd73`), which is what keeps them
compatible.

#### Accessing it

Node addresses come from `cluster_registered/splunk1_cluster_info.yml`, and
provisioning writes SSH aliases, so names work directly:

```bash
grep splunk_admin_password ~/.forgelab/splunk1-credentials.yml   # login: admin
```

| What | Where |
| --- | --- |
| Search head UI — search from here | `http://splunk1-splunk-search-head-1:8000` |
| Cluster manager UI — indexer clustering pages | `http://splunk1-splunk-cluster-manager-1:8000` |
| HEC, for pushing your own events | `http://splunk1-splunk-indexer-1:8088` |
| Forwarder traffic (S2S) | indexers, port 9997 |

#### Using it

Four indexes arrive pre-created, each capped at 5G so a runaway feed rotates
buckets instead of filling your disk:

| Index | Fed by |
| --- | --- |
| `lab_os` | syslog + auth.log from every forwarder |
| `lab_k8s` | `/var/log/pods` container logs from management/compute |
| `lab_hdfs` | NameNode/DataNode logs, multiline-aware |
| `lab_hec` | your own HTTP pushes |

```
index=lab_os | stats count by host, sourcetype
index=lab_k8s | timechart span=1m count by host
index=lab_hdfs "ERROR" | stats count by source
```

Push an event yourself:

```bash
TOKEN=$(grep splunk_hec_token ~/.forgelab/splunk1-credentials.yml | cut -d'"' -f2)
curl -s http://splunk1-splunk-indexer-1:8088/services/collector/event \
  -H "Authorization: Splunk $TOKEN" \
  -d '{"event": {"msg": "hello"}, "sourcetype": "_json"}'
# {"text":"Success","code":0}
```

Indexes and their parsing rules live **only** on the cluster manager, in
`/opt/splunk/etc/manager-apps/_cluster/local/`, and reach the peers via
`splunk apply cluster-bundle`. Editing them on an indexer is how a clustered
deployment drifts apart.

#### Verifying it

`make provision` already runs these three as its Verify stage; run them by
hand when something looks wrong:

```bash
PASSWORD=$(grep splunk_admin_password ~/.forgelab/splunk1-credentials.yml | cut -d'"' -f2)

# 1. search head answering
curl -s -o /dev/null -w "%{http_code}\n" http://splunk1-splunk-search-head-1:8000/en-US/account/login

# 2. both indexer peers Up (self-signed cert, hence -k)
curl -sk -u "admin:$PASSWORD" \
  "https://splunk1-splunk-cluster-manager-1:8089/services/cluster/manager/peers?output_mode=json" \
  | grep -o '"status":"[^"]*"'

# 3. forwarded data actually landing — the check that proves the whole path
curl -sk -u "admin:$PASSWORD" -d 'search=search index=lab_os earliest=-1h | stats count' \
  -d output_mode=json "https://splunk1-splunk-search-head-1:8089/services/search/jobs/export"
```

If (3) is zero while (1) and (2) pass, the forwarders are the suspect. They
have **no management port** — Splunk ships it disabled on the Universal
Forwarder — so check one through its CLI instead of curl:

```bash
ssh splunk1-management-1 \
  "sudo env SPLUNK_USERNAME=admin SPLUNK_PASSWORD='$PASSWORD' /opt/splunkforwarder/bin/splunk list forward-server"
```

Both indexers should be listed under `Active forwards`.

#### Licence

Drop a Splunk Developer Personal License (free, 10GB/day, 6 months, from
https://dev.splunk.com/enterprise/dev_license) at
`~/.forgelab/splunk-dev-license.xml` — outside the repo, like every other key
here — and the role installs it on **every** Enterprise instance.

Not the tidier topology of one licence manager with the rest as peers: that
licence refuses the job, and says so on the peers with `ERROR LMTracker -
failed to send rows, reason='This license does not support being a remote
manager'`, leaving them unlicensed. Each instance holds the licence itself.

Without it each instance runs its own 60-day trial, which expires into
Splunk Free — and Free forbids distributed search and forwarder
authentication, so this topology stops working rather than degrading. Check
which you have with:

```bash
ssh splunk1-splunk-cluster-manager-1 "sudo -u splunk /opt/splunk/bin/splunk list licenses" | grep -E "group_id|quota"
```

### Deprovisioning a cluster

```bash
make deprovision CLUSTER=lab1
```

1. `terraform workspace select <name>` → `destroy -auto-approve`.
2. Backend sweep: `multipass list | grep '<name>-'` → `multipass delete --purge`
   any stragglers the backend's Terraform provider left behind.
3. Removes the generated inventory file and the Terraform workspace.
4. Removes `~/.forgelab/ssh_config.d/<name>.conf` and the cluster's
   `cluster_registered/<name>_cluster_info.yml`.

Deprovision is **idempotent** — safe to run twice, and it's also the
recovery path after a partial/failed provision (see Troubleshooting).

### Why a YAML config per cluster

Each named cluster gets its own `cluster_configs/<name>_cluster.yaml` (node counts,
cpu/mem/disk per node, cluster type, and which technologies it runs),
versioned in the repo. There is no fallback file — a cluster with no config
of its own is a validation error naming the path it looked for, not a silent
default. This keeps sizing decisions in git history and lets you keep
multiple named clusters' configs side by side without collision — each
cluster is also its own Terraform workspace, so state never crosses streams.

Sizing lives in `cluster_configs/<name>_cluster.yaml` — one block per role,
each with `count`, `cpu`, `memory` and `disk`. See `cluster_configs/lab1_cluster.yaml`
for the shipped default.

DC/OS wants more than the shipped k8s sizing; expect a degraded/minimal
install under those defaults on constrained hosts — bump the config if you
have the headroom.

## License ritual

Bamboo Data Center no longer issues new trial licenses (Atlassian stopped
2026-03-30), but a free, published **10-user, 24-hour timebomb key** for
testing Marketplace apps still works for standing up an instance. This lab
uses that key purely for local, personal, non-commercial learning — it is
not an official free tier, and the key **expires 24 hours after
installation**.

- **What:** a timebomb license key from developer.atlassian.com ("Timebomb
  licenses for testing server apps"). You don't have to hunt for it —
  `make license` fetches the current key straight off that page and copies
  it to your clipboard.
- **Where:** pasted into Bamboo's license admin screen on first setup, and
  re-applied there whenever it expires. The `bamboo-home` PVC persists
  everything else (plans, specs, configuration) across relicensing — only
  the license itself needs refreshing.
- **Expiry:** 24 hours after each application. When it lapses, Bamboo
  refuses to run builds until relicensed; the agent stays connected and
  reconnects unaffected once you're relicensed.
- **First-time setup wizard:** run `make license` — it prints the key and
  copies it to your clipboard; paste it into the wizard's license field.
- **After expiry:** run `make relicense` — fetches + copies the current key
  **and** opens Bamboo's license admin page so you can paste and save.

Both commands wrap `infra/scripts/get_license.py`, which scrapes the Bamboo
Data Center 24h key from the Atlassian developer page (needs `curl` +
`python3`, both stock on macOS). Override `LICENSE_LABEL` to pull a
different published key.

Context: the entire Bamboo Data Center product line sunsets, with all DC
licenses expiring March 2029. The CI-agnostic core (see Architecture) means
a Jenkins fallback is a straightforward drop-in if this licensing path ever
dries up sooner.

## VM backend swap boundary

Multipass is the only VM backend implemented, but the boundary is
deliberate: the root Terraform module (`lab/shared/terraform/`) exposes a
fixed contract — cluster name + per-node specs in, node IPs + a rendered
Ansible inventory out — and delegates the actual VM lifecycle to
`lab/shared/terraform/modules/multipass/`.

To add a different backend (UTM, libvirt, etc.) later:

1. Write a sibling module, e.g. `lab/shared/terraform/modules/utm/`,
   implementing the same input/output contract.
2. Flip the `backend` variable to select it.
3. Nothing above the module boundary changes: Ansible only ever consumes the
   generated inventory file, never anything backend-specific, and the
   deprovision backend-sweep step is scoped per backend so cleanup logic
   doesn't need to change either.

## Troubleshooting

**License expired mid-session**
Builds refuse to start; nothing else is affected. Run `make relicense` — it
re-fetches the current timebomb key, copies it to your clipboard, and opens
the license admin page to paste it. The agent stays connected/reconnects on
its own — no need to restart it.

**`make license` prints nothing / errors**
It scrapes the live Atlassian developer page, so it needs network access plus
`curl` and `python3`. If Atlassian renames the license block, override the
label: `LICENSE_LABEL='10 user Bamboo Data Center license, expires in 24 hours' make license`.
As a fallback, open the page yourself (printed in the error) and copy the
"Bamboo Data Center" key by hand.

**`make agent-install` can't find the pod / jar**
It looks for a Bamboo pod in namespace `ci` (override `BAMBOO_NAMESPACE`) and
`kubectl cp`s the installer jar out of it. Make sure `make up` finished and
`kubectl -n ci get pods` shows `bamboo-0` Running. `kubectl cp` needs `tar`
in the pod (the Atlassian image ships it).

**Agent installed but no builds run**
With security token verification on, the agent must be **approved** after it
first connects: Administration → Agents → Agent authentication → approve its
UUID (the `agent-run` log prints the approval URL). It retries every 60s
until approved.

**Multipass flakes / provider errors**
The community-maintained Multipass Terraform provider is known to be a bit
flaky. Scripts pin a known-good provider version and retry `apply` once on
recognized transient errors. If it still fails, check `multipass list` for
stray VMs and consider a manual `multipass delete --purge <name>` before
retrying.

**Ansible says `No route to host` right after a green `terraform apply`**
Check the generated inventory: if several nodes share one `ansible_host` IP,
Multipass gave the whole batch the *same MAC address*, so DHCP issued them a
single lease (`ps -Ao command | grep -o 'mac=[0-9a-f:]*'` shows the duplicate).
This is a multipassd race on concurrent launches, which is why
`tf_apply_retry` runs `terraform apply -parallelism=1` — never remove that
flag. `provision.py` now fails fast on duplicate IPs instead of handing the
broken inventory to Ansible. Recovery: `make deprovision CLUSTER=<name>` and
re-run.

**Partial provision failure**
There is no automatic rollback. Run `make deprovision CLUSTER=<name>` — it's
idempotent and is the designated recovery path: it destroys Terraform state
for that workspace, sweeps any leftover `<name>-*` VMs directly via
`multipass`, and removes the generated inventory, leaving you clean to
provision again.

**`make up` succeeds but Bamboo UI won't load**
Confirm Rancher Desktop's Kubernetes is actually running (`kubectl get
nodes`), then check pod status in the `ci` namespace
(`kubectl -n ci get pods`) — Postgres or Bamboo may still be starting up;
give it a minute and check `kubectl -n ci logs` on the slow pod.

**`ansible-lint` fails with `unknown-module: community.general....`**
See "ansible-galaxy collections" under Prerequisites — Homebrew's
`ansible-lint` doesn't see collections bundled inside Homebrew's `ansible`
formula. `ansible-galaxy collection install community.general
ansible.posix` fixes it for both tools.

**`specs-publish` fails with `Couldn't find credentials file: .credentials`**
`bamboo-specs/.credentials` doesn't exist yet — see "Specs credentials" in
Quick start step 4. If it fails instead with a repository/`defaultRepository`
error, the Linked Repository step (same section) hasn't been done in the
Bamboo UI yet.

**Writing new Ansible `shell` tasks that pipe commands**
`ansible.builtin.shell` runs under `/bin/sh` (dash on Ubuntu) unless told
otherwise, and dash rejects bash-only syntax like `set -o pipefail`. Any
shell task relying on `set -eo pipefail` (or other bashisms) needs an
explicit `args: { executable: /bin/bash }` — this bit the `k8s` role's
containerd-config task live once already (fixed); keep it in mind if you
extend the roles.

**DC/OS install fails with `exec /installer_internal_wrapper: exec format
error`**
Expected on Apple Silicon — see "DC/OS: installer cache and the Apple
Silicon blocker" above. Not fixable from this repo; use `k8s` instead, or
run on amd64 hardware.

## Repository layout

The repo is organized **per plan**. The plan root is
`bamboo-specs/src/main/java/lab/` — referred to as `lab/` throughout this
README and as `$(LAB)` in the Makefile. Each directory under it is one Bamboo
plan, holding its spec *and* every piece of code that plan executes.

```
forge-lab/
├── README.md                       # this file
├── CLAUDE.md                       # repo conventions, commands, layout map
├── Makefile                        # up / down / provision / deprovision / relicense / lint
├── bamboo-specs/                   # Java + Maven (Bamboo Specs plans-as-code)
│   ├── src/main/java/lab/          # PLAN ROOT — one directory per plan
│   │   ├── README.md               # the contract every new plan follows
│   │   ├── shared/                 # used by 2+ plans
│   │   │   ├── SpecConstants.java  # BAMBOO_URL, REPO_NAME
│   │   │   ├── python/forgelab/    # the lab's one library (stdlib only)
│   │   │   ├── terraform/          # main.tf/variables/outputs, modules/multipass/
│   │   │   └── ansible/            # site.yml, roles, generated inventory (gitignored)
│   │   ├── provisioncluster/       # FORGE-PROV
│   │   │   ├── ProvisionClusterSpec.java
│   │   │   └── scripts/            # provision.py, verify.py
│   │   ├── deprovisioncluster/     # FORGE-DEPROV
│   │   │   ├── DeprovisionClusterSpec.java
│   │   │   └── scripts/            # deprovision.py
│   │   └── agentimage/             # AGENT-BUILD
│   │       ├── BuildAgentImageSpec.java
│   │       └── README.md           # build script lives in the bamboo-agent repo
│   └── src/test/                   # java/ spec validation, python/ script tests
├── cluster_configs/                # committed: one <name>_cluster.yaml per cluster
│   └── <name>_cluster.yaml         # type, node sizing, enabled technologies
├── cluster_registered/             # GENERATED: one YAML per live cluster
│   └── <name>_cluster_info.yml     # written by PROV, deleted by DEPROV
├── infra/                          # lab operations, NOT run by any plan
│   ├── helm/                       # Bamboo + Postgres chart values
│   ├── agent/                      # host-local agent install/run scripts
│   └── scripts/                    # license fetch / relicense
└── docs/                           # PRD, tech spec, design docs
```

Python and Terraform living inside a Maven source root is deliberate: one
lookup gets you everything a plan runs. Maven compiles `.java` and ignores the
rest, so it has no effect on the build.

`cluster_registered/` is the one directory here whose contents the pipeline
writes: generated, tracked, and never committed by CI. See
[Cluster info files](#cluster-info-files-cluster_registered).

**Adding a plan?** Follow the contract in
[`bamboo-specs/src/main/java/lab/README.md`](bamboo-specs/src/main/java/lab/README.md):
a directory per plan, its spec and `scripts/` inside it, anything shared by two
or more plans in `lab/shared/`, and anything not executed by a plan in
`infra/`.

See `CLAUDE.md` for the day-to-day command reference and conventions.
