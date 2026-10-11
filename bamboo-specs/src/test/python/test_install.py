import json
import stat
from pathlib import Path

import pytest

import install
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

technologies:
  keycloak:
    enabled: true
"""


def a_config(text=CONFIG):
    from forgelab import clusterconfig

    return clusterconfig.from_text(text, "lab1_cluster.yaml")


NO_TECH = CONFIG.replace("    enabled: true", "    enabled: false")


@pytest.fixture
def lab(tmp_path, monkeypatch):
    recorded = []
    inv_dir = tmp_path / "inventory"
    inv_dir.mkdir()
    (inv_dir / "lab1.ini").write_text(
        "[management]\nlab1-management-1 ansible_host=1.2.3.4\n"
    )

    monkeypatch.setattr(install.paths, "INV_DIR", inv_dir)
    monkeypatch.setattr(install.reachability, "port_open", lambda *a, **kw: True)
    monkeypatch.setattr(install.credentials.paths, "FORGELAB_HOME", tmp_path / "home")
    monkeypatch.setattr(
        install.proc, "run", lambda *a, **kw: recorded.append([str(x) for x in a])
    )
    return type("Lab", (), {"recorded": recorded, "inv_dir": inv_dir, "home": tmp_path})


def test_extra_vars_carries_the_playbook_inputs():
    payload = install.extra_vars("k8s", ["hdfs"], Path("/tmp/r.json"), {})
    assert payload["cluster_type"] == "k8s"
    assert payload["addons"] == "hdfs"
    assert payload["component_report"] == "/tmp/r.json"


def test_extra_vars_joins_an_empty_addon_list_to_an_empty_string():
    assert install.extra_vars("k8s", [], Path("/tmp/r.json"), {})["addons"] == ""


def test_extra_vars_merges_the_secrets_in():
    payload = install.extra_vars(
        "k8s", ["keycloak"], Path("/r"), {"keycloak_admin_password": "x"}
    )
    assert payload["keycloak_admin_password"] == "x"


def test_run_refuses_a_cluster_with_no_inventory(lab):
    with pytest.raises(LabError, match="no inventory for nosuch"):
        install.run("nosuch", a_config())


def test_run_waits_for_ssh_before_it_starts_the_play(lab, monkeypatch):
    """The regression: ansible reports an unroutable host as UNREACHABLE and
    fails the whole run in seconds, so the wait has to happen before the play,
    not inside it."""
    order = []
    monkeypatch.setattr(
        install.reachability, "port_open", lambda ip, **kw: order.append(ip) or True
    )
    monkeypatch.setattr(
        install.proc, "run", lambda *a, **kw: order.append("ansible-playbook")
    )
    install.run("lab1", a_config(NO_TECH))
    assert order == ["1.2.3.4", "ansible-playbook"]


def test_run_refuses_to_start_the_play_when_a_host_never_answers(lab, monkeypatch):
    monkeypatch.setattr(install.reachability, "port_open", lambda *a, **kw: False)
    monkeypatch.setattr(install.reachability, "ATTEMPTS", 1)
    with pytest.raises(LabError, match="no ssh on lab1-management-1"):
        install.run("lab1", a_config(NO_TECH))
    assert lab.recorded == []


def test_run_invokes_the_playbook_with_the_inventory(lab):
    install.run("lab1", a_config(NO_TECH))
    call = lab.recorded[0]
    assert call[0] == "ansible-playbook"
    assert str(lab.inv_dir / "lab1.ini") in call


def test_run_passes_variables_by_file_never_on_the_command_line(lab):
    """argv is world-readable in `ps`; a password must never appear there."""
    install.run("lab1", a_config())
    call = lab.recorded[0]
    assert any(arg.startswith("@") for arg in call)
    assert not any("password" in arg for arg in call)


def test_run_writes_the_credentials_file_for_addons_that_need_one(lab):
    install.run("lab1", a_config())
    assert install.credentials.path("lab1").is_file()


def test_run_writes_no_credentials_file_when_nothing_needs_one(lab):
    install.run("lab1", a_config(NO_TECH))
    assert not install.credentials.path("lab1").exists()


def test_run_deletes_the_variables_file_afterwards(lab):
    install.run("lab1", a_config())
    varsfile = next(a[1:] for a in lab.recorded[0] if a.startswith("@"))
    assert not Path(varsfile).exists()


def test_the_variables_file_is_owner_only_while_it_exists(lab, monkeypatch):
    seen = {}

    def capture(*args, **kwargs):
        varsfile = Path(next(str(a)[1:] for a in args if str(a).startswith("@")))
        seen["mode"] = stat.S_IMODE(varsfile.stat().st_mode)
        seen["payload"] = json.loads(varsfile.read_text())

    monkeypatch.setattr(install.proc, "run", capture)
    install.run("lab1", a_config())
    assert seen["mode"] == 0o600
    assert seen["payload"]["keycloak_admin_password"]


def test_run_does_not_regenerate_passwords_on_a_second_run(lab, monkeypatch):
    """The regression: install.run's call site must reuse credentials.ensure()'s
    already-persisted passwords across runs. A revert of that call site to
    credentials.generate() + credentials.write() mints a fresh password every
    run, locking the lab out of a service (Keycloak) whose own password is
    baked in at first start — and still passes every other test in this file,
    because they don't call run() twice and compare."""
    seen = []

    def capture(*args, **kwargs):
        varsfile = Path(next(str(a)[1:] for a in args if str(a).startswith("@")))
        seen.append(json.loads(varsfile.read_text()))

    monkeypatch.setattr(install.proc, "run", capture)
    install.run("lab1", a_config())
    install.run("lab1", a_config())

    assert seen[0]["keycloak_admin_password"] == seen[1]["keycloak_admin_password"]
    assert seen[0]["keycloak_app_user_password"] == seen[1]["keycloak_app_user_password"]


def test_run_returns_the_component_report_path(lab):
    report = install.run("lab1", a_config(NO_TECH))
    assert report.name == "components.json"


def test_run_deletes_the_variables_file_even_when_the_playbook_fails(lab, monkeypatch):
    """The finally: block must fire on failure too, not just on success —
    a password must not survive a failed run on disk."""
    seen = {}

    def fail(*args, **kwargs):
        seen["varsfile"] = next(str(a)[1:] for a in args if str(a).startswith("@"))
        raise LabError("ansible-playbook failed")

    monkeypatch.setattr(install.proc, "run", fail)
    with pytest.raises(LabError):
        install.run("lab1", a_config())
    assert not Path(seen["varsfile"]).exists()


def test_main_rejects_an_invalid_config(lab, configs_dir):
    (configs_dir / "lab1_cluster.yaml").write_text(CONFIG.replace("type: k8s", "type: swarm"))
    with pytest.raises(LabError, match=r"cluster.type must be one of \[k8s dcos\]"):
        install.main(["lab1"])


def test_main_rejects_a_missing_config(lab, configs_dir):
    with pytest.raises(LabError, match="no config at"):
        install.main(["lab1"])


def test_main_rejects_a_malformed_cluster_name(lab, configs_dir):
    """`make addons` gets the same gate the plan's Validate stage applies."""
    with pytest.raises(LabError, match="cluster_name must match"):
        install.main(["Lab1"])
