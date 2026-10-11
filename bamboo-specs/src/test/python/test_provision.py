"""Validation gates and stage order for provision, with externals stubbed."""

import json
from pathlib import Path

import pytest

import provision
from forgelab import inventory
from forgelab.multipass import Node
from forgelab.proc import LabError

CONFIG = """
cluster:
  type: k8s

cluster_nodes:
  management:
    count: 1
    cpu: 2
    memory: 4G
    disk: 20G
  compute:
    count: 1
    cpu: 2
    memory: 3G
    disk: 20G

technologies:
  hdfs:
    enabled: true
    nodes:
      namenode:
        count: 1
        cpu: 2
        memory: 4G
        disk: 20G
      datanode:
        count: 2
        cpu: 2
        memory: 4G
        disk: 40G
  keycloak:
    enabled: false
"""

BUILT = {
    "lab1-management-1": "192.168.252.10",
    "lab1-compute-1": "192.168.252.11",
    "lab1-hdfs-namenode-1": "192.168.252.20",
    "lab1-hdfs-datanode-1": "192.168.252.21",
    "lab1-hdfs-datanode-2": "192.168.252.22",
}


@pytest.fixture
def lab(tmp_path, monkeypatch, configs_dir):
    calls = []
    existing = {"vms": []}

    class FakeMultipass:
        def list_vms(self, prefix=""):
            return [n for n in existing["vms"] if n.name.startswith(prefix)]

    class FakeTerraform:
        def init(self):
            calls.append(("tf-init",))

        def workspace_select_or_new(self, name):
            calls.append(("tf-workspace", name))

        def apply_retry(self, *args):
            calls.append(("tf-apply", *args))
            # The VMs only exist once terraform has applied — the inventory is
            # rendered from backend state afterwards. The fake builds exactly
            # the nodes map it was handed, which is what the real apply does.
            varsfile = next(a[len("-var-file="):] for a in args
                            if a.startswith("-var-file="))
            payload = json.loads(Path(varsfile).read_text())
            existing["vms"] = [
                Node(name, [BUILT[name]]) for name in payload["nodes"]
            ]

    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG)
    inv_dir = tmp_path / "inventory"
    registry_dir = tmp_path / "cluster_registered"

    monkeypatch.setattr(provision, "multipass", FakeMultipass())
    monkeypatch.setattr(provision, "terraform", FakeTerraform())
    monkeypatch.setattr(provision.proc, "require_tools", lambda *_: None)
    monkeypatch.setattr(provision.paths, "TF_DIR", tmp_path / "terraform")
    monkeypatch.setattr(provision.paths, "INV_DIR", inv_dir)
    # install.run() gates the play on every node answering on ssh; the
    # fake VMs answer nothing, so probe them as up.
    monkeypatch.setattr(
        provision.install.reachability, "port_open", lambda *a, **kw: True
    )
    # Same forgelab.paths module install.py and credentials.py read from too —
    # without this, install.run()'s credentials.write() would land in the
    # real ~/.forgelab instead of the test's tmp_path.
    monkeypatch.setattr(provision.paths, "FORGELAB_HOME", tmp_path / "home")
    monkeypatch.setattr(
        provision.sshconf, "write", lambda c, h: calls.append(("ssh", c, tuple(h)))
    )
    monkeypatch.setattr(
        provision.proc, "run", lambda *a, **kw: calls.append(("run", str(a[0])))
    )
    monkeypatch.setattr(provision.registry.paths, "REGISTRY_DIR", registry_dir)
    real_write = provision.registry.write

    def spy_write(*args, **kwargs):
        calls.append(("registry", args[0]))
        return real_write(*args, **kwargs)

    monkeypatch.setattr(provision.registry, "write", spy_write)

    return type(
        "Lab",
        (),
        {
            "calls": calls,
            "existing": existing,
            "configs": configs_dir,
            "tf_dir": tmp_path / "terraform",
            "inv_dir": inv_dir,
            "registry_dir": registry_dir,
        },
    )


# --- validation gates -----------------------------------------------------


def test_requires_a_cluster_name(lab):
    with pytest.raises(LabError, match="usage:"):
        provision.main([])


@pytest.mark.parametrize("name", ["Lab1", "lab_1"])
def test_rejects_a_malformed_cluster_name(lab, name):
    with pytest.raises(LabError, match=r"cluster_name must match"):
        provision.main([name])


def test_refuses_to_provision_over_existing_vms(lab):
    lab.existing["vms"] = [Node("lab1-management-1", ["1.2.3.4"])]
    with pytest.raises(LabError, match="already exist; deprovision first"):
        provision.main(["lab1"])


def test_rejects_a_missing_config(lab):
    with pytest.raises(LabError, match="no config at .*nosuch_cluster.yaml"):
        provision.main(["nosuch"])


def test_rejects_an_invalid_config(lab):
    (lab.configs / "lab1_cluster.yaml").write_text(CONFIG.replace("type: k8s", "type: swarm"))
    with pytest.raises(LabError, match=r"cluster.type must be one of \[k8s dcos\]"):
        provision.main(["lab1"])


def test_validates_before_touching_the_backend(lab, monkeypatch):
    """A bad plan variable must not cost a multipass round trip.

    The Validate stage catches this earlier still, but `make provision` has no
    stages — this ordering is what makes the CLI fail as fast as the plan.
    """

    def explode(*_args, **_kwargs):
        raise AssertionError("multipass was queried before validation finished")

    monkeypatch.setattr(provision.multipass, "list_vms", explode)
    with pytest.raises(LabError, match="no config at"):
        provision.main(["lab1", "nosuch"])


def test_the_config_variable_selects_a_different_file(lab):
    (lab.configs / "big_cluster.yaml").write_text(CONFIG)
    provision.main(["lab1", "big"])
    assert (lab.tf_dir / ".generated" / "lab1.tfvars.json").is_file()


# --- the generated tfvars -------------------------------------------------


def test_writes_the_nodes_map_terraform_applies(lab):
    provision.main(["lab1"])
    payload = json.loads(
        (lab.tf_dir / ".generated" / "lab1.tfvars.json").read_text()
    )
    assert payload["cluster_name"] == "lab1"
    assert sorted(payload["nodes"]) == sorted(BUILT)
    assert payload["nodes"]["lab1-hdfs-datanode-1"] == {
        "cpus": 2, "memory": "4G", "disk": "40G",
    }


def test_applies_with_the_generated_var_file(lab):
    provision.main(["lab1"])
    apply = next(c for c in lab.calls if c[0] == "tf-apply")
    assert f"-var-file={lab.tf_dir / '.generated' / 'lab1.tfvars.json'}" in apply


def test_the_generated_var_file_survives_the_run(lab):
    """deprovision reads it, so teardown never needs the config file."""
    provision.main(["lab1"])
    assert (lab.tf_dir / ".generated" / "lab1.tfvars.json").is_file()


def test_a_disabled_technology_builds_no_vms(lab):
    text = CONFIG.replace("  hdfs:\n    enabled: true", "  hdfs:\n    enabled: false")
    (lab.configs / "lab1_cluster.yaml").write_text(text)
    provision.main(["lab1"])
    payload = json.loads(
        (lab.tf_dir / ".generated" / "lab1.tfvars.json").read_text()
    )
    assert sorted(payload["nodes"]) == ["lab1-compute-1", "lab1-management-1"]


# --- stage order ----------------------------------------------------------


def test_runs_the_stages_in_order(lab):
    provision.main(["lab1"])
    kinds = [c[0] for c in lab.calls]
    assert kinds.index("tf-init") < kinds.index("tf-workspace") < kinds.index("tf-apply")
    assert kinds.index("tf-apply") < kinds.index("ssh") < kinds.index("run")


def test_writes_the_inventory_before_running_ansible(lab):
    provision.main(["lab1"])
    assert (lab.inv_dir / "lab1.ini").is_file()


def test_registers_the_cluster_only_after_verify(lab):
    provision.main(["lab1"])
    kinds = [c[0] for c in lab.calls]
    # two runs: ansible-playbook, then verify.py — registration comes after both
    assert kinds[-1] == "registry"


def test_leaves_no_cluster_info_when_verify_fails(lab, monkeypatch):
    def fail_on_verify(*args, **kwargs):
        if str(args[0]) != "ansible-playbook":
            raise LabError("nodes not all Ready within timeout")

    monkeypatch.setattr(provision.proc, "run", fail_on_verify)
    with pytest.raises(LabError):
        provision.main(["lab1"])
    assert not (lab.registry_dir / "lab1_cluster_info.yml").exists()


# --- inventory and registry -----------------------------------------------


def test_technology_vms_land_in_their_prefixed_groups(lab):
    provision.main(["lab1"])
    text = (lab.inv_dir / "lab1.ini").read_text()
    assert inventory.group_ips(text, "hdfs_namenode") == ["192.168.252.20"]
    assert inventory.group_ips(text, "hdfs_datanode") == [
        "192.168.252.21", "192.168.252.22",
    ]


def test_the_inventory_carries_the_derived_children(lab):
    provision.main(["lab1"])
    text = (lab.inv_dir / "lab1.ini").read_text()
    assert "[k8s_nodes:children]\nmanagement\ncompute\n" in text
    assert "[hdfs_nodes:children]\nhdfs_namenode\nhdfs_datanode\n" in text


def test_writes_the_cluster_info_file(lab):
    provision.main(["lab1"])
    info = (lab.registry_dir / "lab1_cluster_info.yml").read_text()
    assert "cluster: lab1" in info
    assert "ip: 192.168.252.10" in info
    assert "mem: 4G" in info


def test_the_cluster_info_tells_the_namenode_from_the_datanodes(lab):
    provision.main(["lab1"])
    info = (lab.registry_dir / "lab1_cluster_info.yml").read_text()
    assert "- name: lab1-hdfs-namenode-1\n    role: hdfs-namenode\n" in info
    assert "- name: lab1-hdfs-datanode-1\n    role: hdfs-datanode\n" in info
    assert "disk: 20G" in info
    assert "disk: 40G" in info


# --- what ansible is told -------------------------------------------------


def _capture_extra_vars(monkeypatch):
    """Patch install's ansible-playbook call and return the payload it wrote —
    read from the @varsfile JSON while the file still exists, since
    install.run() deletes it in a `finally` block.

    provision.install.proc and provision.proc are the same forgelab.proc module
    object, so this fake also receives the verify.py call; only the
    ansible-playbook call has an "@varsfile" argument, so anything else is left
    alone (a no-op stand-in, same as the rest of the suite's fakes).
    """
    seen = {}

    def fake_run(*args, **kwargs):
        varsfile = next((str(a)[1:] for a in args if str(a).startswith("@")), None)
        if varsfile is not None:
            seen["payload"] = json.loads(Path(varsfile).read_text())

    monkeypatch.setattr(provision.install.proc, "run", fake_run)
    return seen


def test_tells_ansible_where_to_report_components(lab, monkeypatch):
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1"])
    assert "component_report" in seen["payload"]


def test_passes_the_configs_cluster_type_to_ansible(lab, monkeypatch):
    (lab.configs / "lab1_cluster.yaml").write_text(CONFIG.replace("type: k8s", "type: dcos"))
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1"])
    assert seen["payload"]["cluster_type"] == "dcos"


def test_passes_the_enabled_technologies_as_addons(lab, monkeypatch):
    """site.yml still gates its roles on the `addons` comma string."""
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1"])
    assert seen["payload"]["addons"] == "hdfs"


def test_a_config_with_nothing_enabled_passes_an_empty_addons(lab, monkeypatch):
    text = CONFIG.replace("  hdfs:\n    enabled: true", "  hdfs:\n    enabled: false")
    (lab.configs / "lab1_cluster.yaml").write_text(text)
    seen = _capture_extra_vars(monkeypatch)
    provision.main(["lab1"])
    assert seen["payload"]["addons"] == ""
