"""make bamboo: the decisions it makes before handing over to the agent."""

import pytest
import start_lab
from forgelab.proc import LabError


def test_reuses_a_port_forward_that_is_already_running():
    assert start_lab.decide_forward({8085: True, 54663: True}) == "reuse"


def test_starts_its_own_forward_when_nothing_is_listening():
    assert start_lab.decide_forward({8085: False, 54663: False}) == "start"


def test_refuses_a_half_forwarded_pair():
    # One port up and one down is someone else's listener or a dead `make ui`;
    # starting a second forward would bind-conflict on the port already taken.
    with pytest.raises(LabError) as err:
        start_lab.decide_forward({8085: True, 54663: False})
    assert "8085" in str(err.value)


def test_waits_until_every_port_answers():
    answers = iter([False, False, True])
    ports = start_lab.wait_for_ports(
        [8085], timeout=5, probe=lambda port: next(answers), sleep=lambda _: None,
        now=iter([0, 1, 2, 3]).__next__,
    )
    assert ports is True


def test_gives_up_once_the_timeout_passes():
    assert start_lab.wait_for_ports(
        [8085], timeout=5, probe=lambda port: False, sleep=lambda _: None,
        now=iter([0, 6]).__next__,
    ) is False


def test_installs_the_agent_only_when_the_jar_is_missing(tmp_path):
    jar = tmp_path / "agent-installer.jar"
    assert start_lab.needs_install(jar) is True
    jar.write_bytes(b"x")
    assert start_lab.needs_install(jar) is False


def test_an_empty_jar_counts_as_missing(tmp_path):
    jar = tmp_path / "agent-installer.jar"
    jar.touch()
    assert start_lab.needs_install(jar) is True
