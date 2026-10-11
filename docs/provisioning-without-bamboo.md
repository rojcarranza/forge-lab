# Provisioning and deprovisioning without Bamboo

This guide is the standalone path: building and tearing down a cluster with
`make` alone, on a host that has no Bamboo server, no host agent, and no
plan run. It is the whole lifecycle in four commands.

Nothing here is a reimplementation of the pipeline. The PROV and DEPROV plans
run the *same* entrypoint scripts under
`bamboo-specs/src/main/java/lab/provisioncluster/scripts/` and
`.../deprovisioncluster/scripts/`; the Bamboo specs only wrap them in stages
and an `agent.role=host` requirement. `make provision` and the plan therefore
fail on the same input, with the same message, and produce the same files.

For the pipeline-oriented view of the same work — plan variables, the Validate
stage, per-technology health checks — see
[`provision-usage.md`](provision-usage.md). For what to *do* with a cluster
once it is up, see [`using-cluster-addons.md`](using-cluster-addons.md).

Throughout, `lab1` is the example cluster name.

## 1. What you need on the host

The scripts shell out to four tools and fail immediately, by name, if any is
missing:

```
terraform   multipass   ansible-playbook   ssh
```

Plus Python 3 to run the entrypoints themselves. The lab's Python library is
standard-library only, so there is no virtualenv to create and nothing to
`pip install`.

Two files outside the repository:

| Path | Needed for | How to create it |
| --- | --- | --- |
| `~/.forgelab/id_ed25519` (+ `.pub`) | Every VM's `ubuntu` login. Baked into cloud-init at launch, and used by Ansible and by the generated ssh config | `mkdir -p ~/.forgelab && ssh-keygen -t ed25519 -N '' -f ~/.forgelab/id_ed25519` |
| `~/.forgelab/splunk-dev-license.xml` | Splunk clusters only | Download a Developer Personal License from <https://dev.splunk.com/enterprise/dev_license>, save it there, `chmod 600` |

`make setup` creates the ssh key too, but it also creates the `ci` namespace
and adds Helm repos, so on a host with no Kubernetes use the `ssh-keygen` line
above instead.

Finally, check the host has the RAM. `cluster_configs/lab1_cluster.yaml` is 11
VMs reserving 48G; section 3 below shows how to print the exact roll-up before
building anything.

## 2. The four commands

```bash
make provision   CLUSTER=lab1               # build from cluster_configs/lab1_cluster.yaml
make provision   CLUSTER=lab2 CONFIG=lab1   # build lab2 from lab1's config
make addons      CLUSTER=lab1               # re-run the install stage only
make deprovision CLUSTER=lab1               # tear everything down
```

`CLUSTER` is required and must match `^[a-z0-9-]+$`. `CONFIG` picks which
`cluster_configs/<name>_cluster.yaml` to build from; empty means the config
named after the cluster. There is no fallback — a missing config is an error
naming the path it looked for, not a smaller cluster.

The config file is the single source of truth for a cluster's type, its node
sizing, and which technologies it runs. Nothing on the command line overrides a
value inside it; `CONFIG=` only chooses *which* file to read.

Provisioning refuses to start when VMs with the `<cluster>-` prefix already
exist, so deprovision before rebuilding under the same name.

## 3. Dry-run the sizing first

The validation entrypoint resolves the config and prints the resulting cluster
without touching Terraform or multipass:

```bash
bamboo-specs/src/main/java/lab/provisioncluster/scripts/validate_prov.py lab1
```

```
==> cluster_name   lab1
==> cluster_type   k8s
==> technologies   hdfs,keycloak,splunk
==> config         lab1_cluster.yaml

ROLE                     N CPU MEM   DISK
management               1   2 4G    20G
compute                  2   2 3G    20G
hdfs-namenode            1   2 4G    20G
hdfs-datanode            3   2 4G    40G
splunk-cluster-manager   1   2 4G    20G
splunk-indexer           2   4 6G    60G
splunk-search-head       1   4 6G    30G

==> total          11 VMs, 28 vCPU, 48G RAM
```

Cheap to run, and it catches a typo'd cluster name, a missing config, or an
over-sized cluster in seconds rather than eight VMs into an apply.

## 4. What `make provision` actually does

Five stages, in `provision.py`, in this order:

1. **Validate** — resolve `CLUSTER`/`CONFIG`, load and validate the config,
   check the four tools are on `PATH`, refuse if VMs with the prefix exist.
2. **Terraform apply** — write
   `.../shared/terraform/.generated/<cluster>.tfvars.json` from the config,
   `terraform init`, select or create the workspace named after the cluster,
   then apply with `-parallelism=1`. That flag is load-bearing: concurrent
   `multipass launch` calls race in multipassd's MAC allocation, every VM of
   the batch comes up with the same MAC, and DHCP hands them one shared lease.
   A failed apply is retried once after 10s.
3. **Inventory** — the Terraform multipass provider does not expose VM
   addresses, so the Ansible inventory is rendered from live `multipass list`
   state, then checked for duplicate or missing IPs (the MAC race above,
   caught explicitly). The per-cluster ssh config is written at the same point.
4. **Install** — wait until every node answers on TCP 22 (60 attempts, 5s
   apart), generate or reuse the cluster's credentials, then run `site.yml`
   with the technology list as `addons`. The wait exists because
   `multipass launch` returns before the host can necessarily route to the new
   VM; without it the play fails every host at once with `No route to host`
   seconds in, discarding VMs that were seconds from ready.
5. **Verify, then register** — `verify.py` polls the cluster until each enabled
   technology reports healthy (all k8s nodes `Ready` and a default
   StorageClass; a Keycloak token from the seeded realm; live HDFS DataNodes;
   OpenSearch cluster health and a document count; Splunk indexer peers up and
   forwarded events searchable). Only then is
   `cluster_registered/<cluster>_cluster_info.yml` written, so the registry
   only ever lists clusters that passed.

A failure in any stage leaves the VMs standing. That is deliberate — the box is
there to inspect — but it means `make deprovision` is the way back to a clean
slate, not a re-run of `make provision`.

## 5. What ends up on disk

| Path | Written by | Removed by |
| --- | --- | --- |
| `.../shared/terraform/.generated/<cluster>.tfvars.json` | provision | deprovision |
| `.../shared/terraform/.generated/<cluster>-cloud-init.yaml` | Terraform | `terraform destroy` |
| `.../shared/terraform/terraform.tfstate.d/<cluster>/` | Terraform workspace | deprovision |
| `.../shared/ansible/inventory/<cluster>.ini` | provision | deprovision |
| `~/.forgelab/ssh_config.d/<cluster>.conf` | provision | deprovision |
| `~/.forgelab/<cluster>-credentials.yml` (0600) | install | deprovision |
| `cluster_registered/<cluster>_cluster_info.yml` | provision, last | deprovision |

None of the generated Terraform or Ansible files are committed. The cluster
info file *is* tracked but the pipeline never commits it — you do.

### Two things worth knowing

**ssh works by node name.** The first provision adds
`Include ~/.forgelab/ssh_config.d/*.conf` to the top of `~/.ssh/config`
(backing up the original to `~/.ssh/config.forgelab.bak`), so
`ssh lab1-management-1` and `ssh 192.168.252.157` both land as `ubuntu` with
the lab key. The Include goes *above* any existing `Host *` block, because ssh
keeps the first value it finds for each option.

**Where the registry lands.** `cluster_registered/` resolves in this order:
`$FORGELAB_REGISTRY_DIR`, then `~/.forgelab/registry_dir` (a one-line file),
then this checkout. On a host that has ever run `make agent-run`, that pointer
file exists and wins — so a hand-run `make provision` writes into whatever it
names, not necessarily the checkout you are standing in. Check with:

```bash
cat ~/.forgelab/registry_dir      # absent on a Bamboo-free host: the checkout wins
```

Override it for one run with `FORGELAB_REGISTRY_DIR=/path make provision ...`.

## 6. Iterating on an Ansible role

`make addons CLUSTER=lab1` re-runs the install stage against the existing
inventory: same ssh wait, same credentials, same `site.yml`, same technology
list from the config. It touches neither Terraform nor the VMs, which makes it
the fast loop while editing a role. Roles are written to be idempotent, so
re-running is safe.

It does **not** verify or register. After a successful `make addons` on a
cluster that never finished provisioning, run the verifier by hand if you want
the health checks:

```bash
bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py lab1 k8s hdfs,keycloak,splunk
```

The arguments are cluster name, cluster type, and the comma-separated list of
enabled technologies — exactly what `validate_prov.py` prints.

## 7. Tearing down

```bash
make deprovision CLUSTER=lab1
```

Four steps, each safe to repeat:

1. `terraform destroy` in the cluster's workspace, then delete the workspace.
   Skipped with a message when no such workspace exists. The destroy reads the
   generated `.tfvars.json` when it is there and `nodes={}` when it is not —
   destroy works from state, not from variables, so deleting or renaming a
   cluster config can never strand a running cluster.
2. A backend sweep: `multipass delete --purge` on anything still named
   `<cluster>-*`, then `multipass purge`. This is the real guarantee; the
   destroy above is best-effort.
3. Delete the generated inventory, the generated tfvars, and the ssh config.
4. Delete the cluster info file and the credentials file. The run prints the
   registry path it cleared, so a no-op is visible rather than silent.

Deprovision takes only `CLUSTER` — never a config — so it works on a cluster
whose config file has since changed or been deleted.

If something is truly wedged, the manual equivalent is:

```bash
multipass list | grep '^lab1-'
multipass delete --purge lab1-management-1 ...   # then: multipass purge
rm -f ~/.forgelab/ssh_config.d/lab1.conf ~/.forgelab/lab1-credentials.yml
rm -f cluster_registered/lab1_cluster_info.yml
rm -f bamboo-specs/src/main/java/lab/shared/ansible/inventory/lab1.ini
terraform -chdir=bamboo-specs/src/main/java/lab/shared/terraform workspace delete lab1
```

## 8. Troubleshooting

| Symptom | Cause | What to do |
| --- | --- | --- |
| `missing tool: terraform` | One of the four required binaries is not on `PATH` | Install it; the check is up front, before anything is built |
| `no config at .../cluster_configs/lab1_cluster.yaml (available: …)` | `CLUSTER`/`CONFIG` names a config that does not exist | The error lists the configs that *do* exist — pick one, or add the file |
| `VMs with prefix 'lab1-' already exist; deprovision first` | A previous run's VMs are still there | `make deprovision CLUSTER=lab1`, or pick another `CLUSTER` name |
| `duplicate node IP(s) … multipass assigned the same MAC` | The multipassd MAC race | Deprovision and re-run. If it recurs, confirm `-parallelism=1` is still in `terraform.apply_retry` |
| `inventory … has no hosts (multipass reported no IPs)` | VMs launched but never got a lease | Deprovision and re-run; check `multipass list` for VMs stuck in `Starting` |
| `no ssh on <node> (<ip>) within 300s — the VMs are up but the host cannot route to them` | The host's route or ARP entry for the VM subnet never settled | Check `multipass list` shows the VM `Running` on that IP, then `nc -z <ip> 22` from the host. A macOS `vmnet` bridge that has gone stale is fixed by restarting multipassd (`sudo launchctl kickstart -k system/com.canonical.multipassd`) |
| Ansible `UNREACHABLE! … No route to host` mid-play | An ARP entry dropped while the play ran | `ansible.cfg` retries the connection three times; if it still fails, re-run `make addons` — the roles are idempotent |
| `nodes not all Ready within timeout` in verify | The k8s cluster came up but a node never joined | `ssh lab1-management-1 kubectl get nodes` and read the failing node's kubelet log. The VMs are left standing for exactly this |
| Splunk instances unlicensed, distributed search refused | No `~/.forgelab/splunk-dev-license.xml` at install time | Add the licence, then `make addons CLUSTER=lab1` |

## 9. How this differs from a plan run

| | `make` on the host | PROV / DEPROV plan |
| --- | --- | --- |
| Where it runs | Your shell, this checkout | The host agent, in Bamboo's own throwaway checkout |
| Agent requirement | none | `agent.role=host`, so it never lands on the containerized CI agent |
| Validation | The entrypoint's first call | A separate `Validate` stage with no `agent.role`, so bad input fails on any free agent |
| Registry location | This checkout, unless `~/.forgelab/registry_dir` points elsewhere | Always the pointer file — a repo-relative path would strand PROV's file where DEPROV could never delete it |
| Scripts executed | Identical | Identical |

That last row is the point: there is no Bamboo-only code path to fall out of
step with. Anything reproducible in the pipeline is reproducible here.
