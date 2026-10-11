"""The agent's environment handoff: one registry location for every plan."""

import run_agent


def test_points_jobs_at_the_clone_the_agent_runs_from():
    env = run_agent.registry_dir_env({"PATH": "/usr/bin"})
    assert env["FORGELAB_REGISTRY_DIR"] == str(
        run_agent.CLONE_ROOT / "cluster_registered"
    )
    assert env["PATH"] == "/usr/bin"


def test_leaves_an_explicit_setting_alone():
    env = run_agent.registry_dir_env({"FORGELAB_REGISTRY_DIR": "/elsewhere"})
    assert env["FORGELAB_REGISTRY_DIR"] == "/elsewhere"


def test_the_clone_root_is_a_checkout():
    assert (run_agent.CLONE_ROOT / "Makefile").is_file()


def test_seeds_the_host_pointer_so_a_running_agent_picks_it_up(tmp_path, capsys):
    pointer = tmp_path / "forgelab" / "registry_dir"
    run_agent.seed_registry_pointer(pointer, "/clone/cluster_registered")
    assert pointer.read_text() == "/clone/cluster_registered\n"
    assert "Seeded cluster registry location" in capsys.readouterr().out


def test_rewrites_a_pointer_that_names_somewhere_else(tmp_path):
    pointer = tmp_path / "registry_dir"
    pointer.write_text("/old/cluster_registered\n")
    run_agent.seed_registry_pointer(pointer, "/clone/cluster_registered")
    assert pointer.read_text() == "/clone/cluster_registered\n"


def test_says_nothing_when_the_pointer_already_agrees(tmp_path, capsys):
    pointer = tmp_path / "registry_dir"
    pointer.write_text("/clone/cluster_registered\n")
    run_agent.seed_registry_pointer(pointer, "/clone/cluster_registered")
    assert capsys.readouterr().out == ""


def test_the_console_command_carries_agent_home_and_the_server_url():
    argv = run_agent.agent_argv()
    assert argv[0] == "java"
    assert argv[1] == f"-Dbamboo.home={run_agent.AGENT_HOME}"
    assert argv[-2:] == [f"{run_agent.BAMBOO_URL}/agentServer/", "console"]
