# Running the Provision pipeline

This is the operational guide for provisioning a cluster — what to type into
the Bamboo plan variables, what the equivalent `make` invocation is, what
number of VMs to expect, and how to confirm afterwards that each technology
actually installed.

It assumes the lab itself is already up (`make bootstrap`, host agent
running and approved). For what to *do* with the technologies once they're
verified — Keycloak realms, HDFS paths, Dashboards index patterns — see
[`using-cluster-addons.md`](using-cluster-addons.md). For the design behind
any of this, see
[`superpowers/specs/2026-07-23-forge-lab-design.md`](superpowers/specs/2026-07-23-forge-lab-design.md).

Throughout, `lab1` is the example cluster name.

## 1. The plan variables

The Provision plan (PROV) takes two variables. Run it manually from Bamboo
and fill them in:

| Variable | Default | Meaning |
| --- | --- | --- |
| `cluster_name` | `lab1` | Names every VM (`lab1-management-1`, …), the Terraform workspace, and the cluster info file |
| `cluster_config` | *(empty)* | Which `cluster_configs/<name>_cluster.yaml` to build from. Empty means the config named after the cluster |

A cluster's type, its node sizing, and which technologies it runs all live in
that one YAML file — see [`README.md`'s "Cluster configs"
section](../README.md#cluster-configs) for its shape. There is nothing left
for a plan variable to override: `cluster_config` only ever picks *which*
file to read, never a value inside it. Point two different cluster names at
the same file with `cluster_config`, e.g. building `lab2` from `lab1`'s
config to compare runs without hand-copying sizing.

### There is no fallback

`clusterconfig.load()` looks for exactly one file,
`cluster_configs/<cluster_config>_cluster.yaml`. If it isn't there, the run
dies naming the path it looked for and listing what *is* available — there
is no default config it quietly falls back to. A missing config is a
provisioning error, not a smaller cluster.

### Failing fast

Both PROV and DEPROV open with a `Validate` stage that carries **no**
`agent.role` requirement, so a bad cluster name or config fails in seconds on
whatever agent is free rather than queueing behind the host agent. The
entrypoint scripts call the same `planvars`/`clusterconfig` code first thing,
so `make provision` fails identically and with the same message.

## 2. The same thing from the shell

```
make provision CLUSTER=lab1                 # builds from cluster_configs/lab1_cluster.yaml
make provision CLUSTER=lab2 CONFIG=lab1     # builds lab2 from lab1's config
```

`CONFIG=` is the same override as the `cluster_config` plan variable and
follows the same rule — empty means "the config named after `CLUSTER`".

Related targets:

```
make deprovision CLUSTER=lab1               # tear down, including the cluster info file
make addons CLUSTER=lab1                    # re-run the install stage only
```

`make addons` re-applies `site.yml` against the existing inventory, using the
same cluster's config to decide which technologies' roles to run. It touches
neither Terraform nor the VMs, which makes it the fast loop for iterating on
an Ansible role.

These targets run the same entrypoint scripts the plans do, so nothing about
them needs Bamboo to be up. For the standalone lifecycle on its own — host
prerequisites, what each stage writes, teardown and troubleshooting without a
plan run — see
[`provisioning-without-bamboo.md`](provisioning-without-bamboo.md).

Provisioning refuses to start if VMs with the `<cluster>-` prefix already
exist, so deprovision before re-running the same name.

## 3. How many VMs to expect

A technology's `enabled` flag gates its Ansible role *and* is the only thing
that puts its nodes in the Terraform `nodes` map, so sizing and enablement
cannot disagree: disabling `hdfs` doesn't build empty HDFS nodes, it builds
none.

| Technology | VM roles | Count |
| --- | --- | --- |
| `hdfs` | `hdfs-namenode`, `hdfs-datanode` | as configured; exactly one NameNode |
| `opensearch` | `opensearch-master` | as configured |
| `keycloak` | *none* | 0 — it runs as pods on the k8s cluster the cluster nodes already form |
| `splunk` | `splunk-cluster-manager`, `splunk-indexer`, `splunk-search-head` | exactly one manager, exactly one search head, at least two indexers |

`clusterconfig` enforces the NameNode count itself:
`technologies.hdfs.nodes.namenode.count` must be `1`, because non-HA HDFS has
exactly one.

It enforces Splunk's counts for the same reason — they are fixed by the
product, not by taste. One cluster manager per cluster; one search head,
because a search head *cluster* needs three members and a deployer this lab
does not build; and two indexers minimum, because the manager runs
`replication_factor = 2` and a peer cannot replicate to itself.

`keycloak` additionally requires `cluster.type: k8s`. Its role applies
manifests with `kubectl --kubeconfig /etc/kubernetes/admin.conf`, which only a
kubeadm control plane has, so a dcos cluster is rejected up front rather than
halfway through the install.

The shipped `cluster_configs/lab1_cluster.yaml` declares 1 management + 2
compute + 1 hdfs-namenode + 3 hdfs-datanode + 1 splunk-cluster-manager + 2
splunk-indexer + 1 splunk-search-head = **11 VMs**, reserving 48G of RAM
(4 + 2×3 + 4 + 3×4 + 4 + 2×6 + 6). Check the host has it before starting.
`opensearch1_cluster.yaml` is the OpenSearch variant at 10 VMs and 44G; see
[`../cluster_configs/README.md`](../cluster_configs/README.md) for the full
set. See "Checking a cluster's size before building it" below
for the exact roll-up, straight from `validate_prov.py`.

Note that `kubectl get nodes` on that 11-VM cluster shows **3** nodes. Only
the management and compute nodes join Kubernetes; the HDFS and Splunk VMs
are plain hosts running systemd services. That is correct, not a partial
install.

### Checking a cluster's size before building it

The Validate stage prints the whole resolved run, including a per-role
roll-up and the totals:

```
$ bamboo-specs/src/main/java/lab/provisioncluster/scripts/validate_prov.py lab1
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

It runs on any agent and needs nothing but Python and the checkout, so a
mis-sized cluster costs seconds rather than a failed apply. `make provision`
performs the same checks before touching multipass.

## 4. What a successful run produces

The stages are Validate → Provision (Terraform) → Install (Ansible) →
Verify → Register, in that order. Register runs last so the registry only
ever lists clusters that passed verification.

A clean run ends with:

```
==> verify: waiting for all nodes Ready (timeout 300s)
==> verify: keycloak realm at http://192.168.252.127:30080/realms/forgelab
==> verify: 3 live datanodes on 192.168.252.124
==> verify: splunk search head at http://192.168.252.122:8000
==> verify: 2 indexer peers up on 192.168.252.123
splunk indexed 4666 events into lab_os
==> inventory: .../ansible/inventory/lab1.ini
==> ssh config: /Users/roj/.forgelab/ssh_config.d/lab1.conf (try: ssh lab1-management-1)
==> credentials: /Users/roj/.forgelab/lab1-credentials.yml
==> cluster info: .../cluster_registered/lab1_cluster_info.yml
==> cluster 'lab1' provisioned and verified
```

Four artifacts to know about:

- `cluster_registered/<cluster>_cluster_info.yml` — every node with role, IP
  and sizing, plus a `components` list with versions and URLs. Tracked but
  never committed by CI; committing it is yours to do.
- `~/.forgelab/ssh_config.d/<cluster>.conf` — included from `~/.ssh/config`,
  which is what makes `ssh lab1-management-1` work as `ubuntu` with the lab key.
- `~/.forgelab/<cluster>-credentials.yml` — Keycloak passwords, mode `0600`,
  never in the repo. Absent if no technology needed secrets.
- `bamboo-specs/src/main/java/lab/shared/ansible/inventory/<cluster>.ini` —
  generated from live Multipass state, since the provider doesn't expose IPs.

The IPs below come from that cluster info file. Read it, or use the `ssh`
aliases and skip looking them up.

## 5. Checking what installed

### Everything at once

The one-shot check is the pipeline's own verify stage, re-run by hand:

```
bamboo-specs/src/main/java/lab/provisioncluster/scripts/verify.py lab1 k8s hdfs,keycloak,opensearch
```

Exit 0 means all of it is healthy: every k8s node `Ready`, a default
StorageClass, k9s present, the Keycloak issuer answering, the expected
number of live HDFS datanodes, and a 3-node OpenSearch cluster.

To see what provision *recorded* rather than re-probing:

```
cat cluster_registered/lab1_cluster_info.yml
```

The `components:` block lists each installed piece with its version and
endpoints. If a technology isn't in there, it wasn't installed.

### Kubernetes

```
ssh lab1-management-1 "kubectl get nodes"
```

```
NAME                  STATUS   ROLES           AGE   VERSION
lab1-compute-1        Ready    <none>          34m   v1.30.14
lab1-compute-2        Ready    <none>          34m   v1.30.14
lab1-management-1     Ready    control-plane   34m   v1.30.14
```

k9s, which only exists on the management node (compute nodes have no
kubeconfig):

```
ssh lab1-management-1 "k9s version --short"
```

### Keycloak

Keycloak runs as pods, so `systemctl` will tell you nothing. Check the pods:

```
ssh lab1-management-1 "kubectl get pods -n keycloak"
```

```
NAME                           READY   STATUS    RESTARTS   AGE
keycloak-db-69f87b4bc5-s8ndk   1/1     Running   0          33m
keycloak-ff944d649-8kscm       1/1     Running   0          33m
```

The definitive check — the realm answering over the NodePort, which is what
verify.py polls:

```
curl -s "http://<management-ip>:30080/realms/forgelab/.well-known/openid-configuration" | head -c 120
```

```
{"issuer":"http://192.168.252.98:30080/realms/forgelab","authorization_endpoint":...
```

Admin console at `http://<management-ip>:30080`, user `admin`:

```
grep keycloak_admin_password ~/.forgelab/lab1-credentials.yml
```

End-to-end, proving realm + client `app` + user `labuser` together. The
password goes over stdin rather than in a `-d` flag, because `ps` shows argv
to every user on the box:

```
PASSWORD=$(grep keycloak_app_user_password ~/.forgelab/lab1-credentials.yml | cut -d'"' -f2)

printf '%s' "$PASSWORD" | curl -s -X POST \
  http://<management-ip>:30080/realms/forgelab/protocol/openid-connect/token \
  -d grant_type=password -d client_id=app -d username=labuser \
  --data-urlencode password@-
```

A JSON body containing `access_token` means the whole chain works.

### HDFS

The NameNode is `hdfs-namenode-1` and runs no DataNode; blocks live on
`hdfs-datanode-1` and up.

```
ssh lab1-hdfs-namenode-1 "hdfs dfsadmin -report | head -12"
```

```
Configured Capacity: 121451827200 (113.11 GB)
DFS Used%: 0.00%
Missing blocks: 0
```

`WARN util.NativeCodeLoader: Unable to load native-hadoop library for your
platform` on the first line is normal and not a failure.

Filesystem responding, and the seeded path present:

```
ssh lab1-hdfs-namenode-1 "hdfs dfs -ls /user/app"
```

A write/read round trip, if you want more than liveness:

```
ssh lab1-hdfs-namenode-1 "echo hello | hdfs dfs -put -f - /user/app/hello.txt && hdfs dfs -cat /user/app/hello.txt"
```

Per-node daemons, when the report shows fewer datanodes than expected:

```
for n in 1 2 3; do ssh lab1-hdfs-datanode-$n "systemctl is-active hdfs-datanode"; done
```

NameNode UI: `http://<hdfs-namenode-1-ip>:9870`

### OpenSearch and Dashboards

Node 1 runs both OpenSearch and Dashboards; nodes 2 and 3 run OpenSearch
only. There is no password — the security plugin is disabled outright.

```
curl -s "http://<opensearch-master-1-ip>:9200/_cluster/health?pretty"
```

```
"cluster_name" : "forgelab",
"status" : "green",
"number_of_nodes" : 3,
"number_of_data_nodes" : 3,
```

Want `green` and the full node count. Yellow means replicas are unassigned;
red means data is missing.

Which nodes actually joined:

```
curl -s "http://<opensearch-master-1-ip>:9200/_cat/nodes?v"
```

Dashboards answering:

```
curl -s -o /dev/null -w "%{http_code}\n" "http://<opensearch-master-1-ip>:5601/api/status"
```

```
200
```

Per-node service, when a node is missing from `_cat/nodes`:

```
for n in 1 2 3; do ssh lab1-opensearch-master-$n "systemctl is-active opensearch"; done
```

Browser UI at `http://<opensearch-master-1-ip>:5601`. Note that Discover
looks empty on a fresh cluster until you create the `forgelab-logs*` index
pattern by hand — that's a first-run step, not a broken install. See
[`using-cluster-addons.md`](using-cluster-addons.md) for it.

In zsh, quote any URL containing `?` or `&`, or you get `no matches found`
from globbing before curl ever runs.

### Splunk

Three checks, in the order the Verify stage runs them: the search head is
up, both indexer peers have joined the manager, and forwarded events are
actually arriving.

```
curl -s -o /dev/null -w "%{http_code}\n" "http://<splunk-search-head-1-ip>:8000/en-US/account/login"
```

```
200
```

Peers, from the cluster manager (self-signed cert, hence `-k`):

```
PASSWORD=$(grep splunk_admin_password ~/.forgelab/<cluster>-credentials.yml | cut -d'"' -f2)
curl -sk -u "admin:$PASSWORD" \
  "https://<splunk-cluster-manager-1-ip>:8089/services/cluster/manager/peers?output_mode=json" \
  | grep -o '"status":"[^"]*"'
```

Want one `"status":"Up"` per indexer. A peer that registered once and then
died still appears in the list with another status, so count the `Up`s, not
the entries.

Data actually flowing — the check that proves the forwarders, not just the
cluster:

```
curl -sk -u "admin:$PASSWORD" \
  -d 'search=search index=lab_os earliest=-1h | stats count' \
  -d output_mode=json \
  "https://<splunk-search-head-1-ip>:8089/services/search/jobs/export"
```

A non-zero `count` means events from other VMs reached the indexers. Zero
with a healthy cluster points at the forwarders. Check one — the forwarder
has no management port to curl, so this goes through its CLI:

```
ssh <cluster>-management-1 \
  "sudo env SPLUNK_USERNAME=admin SPLUNK_PASSWORD='$PASSWORD' /opt/splunkforwarder/bin/splunk list forward-server"
```

Both indexers should appear under `Active forwards`. Anything under
`Configured but inactive forwards` means the forwarder knows the address but
cannot deliver to it.

Per-node service, when an instance is missing:

```
ssh lab1-splunk-indexer-1 "systemctl is-active splunk"
ssh lab1-management-1 "systemctl is-active splunkforwarder"
```

Splunk here runs its amd64 build under emulation, so a service start takes
about two minutes and searches run roughly 5x slower than native. Slow is
expected; failed is not.

## 6. When the VM count is wrong

Symptom: the cluster comes up healthy, but only management and compute VMs
exist and no technology shows up anywhere.

Work through it in this order — the first two account for nearly all of it:

1. **Is the technology actually `enabled: true` in the config?** Check the
   run's first log line, which states exactly what was resolved and from
   which file:

   ```
   ==> provisioning 'lab1' type=k8s technologies=hdfs,opensearch,keycloak config=lab1_cluster.yaml
   ```

   `technologies=none` there means nothing is enabled — either every
   technology block in the config has `enabled: false`, or the config has no
   `technologies` section at all.

2. **Is `cluster_config` pointing at the file you think it is?** `config=` in
   that same line names the actual file read. An empty `cluster_config`
   resolves to the config named after `cluster_name`, which is easy to get
   wrong when building one cluster's VMs from another's config with `CONFIG=`.

3. **Check `components:` in the cluster info file.** Only kubernetes,
   containerd, flannel, k9s and local-path-provisioner means no technology's
   role ever ran — this is a resolution problem, not an installation
   failure.

4. **Is `~/.forgelab/<cluster>-credentials.yml` missing?** Keycloak
   generates it. No file, no Keycloak.

A genuine install failure looks different: the Ansible PLAY RECAP shows
non-zero `failed=` or `unreachable=`, or the verify stage dies with a
specific message (`nodes not all Ready within timeout`, and so on). Those
are worth debugging. A quiet, healthy, undersized cluster is almost always
a technology left disabled in the config.
