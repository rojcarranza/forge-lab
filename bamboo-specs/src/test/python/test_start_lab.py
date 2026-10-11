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


LSOF = "p40237\ncbamboostub\nf3\n"


def test_parses_the_process_holding_a_port_from_lsof():
    assert start_lab.parse_lsof(LSOF) == "bamboostub (pid 40237)"


def test_parses_nothing_from_empty_lsof_output():
    assert start_lab.parse_lsof("") == ""


def test_a_half_forwarded_pair_names_the_process_holding_the_port():
    with pytest.raises(LabError) as err:
        start_lab.decide_forward({8085: True, 54663: False},
                                 holder=lambda port: "bamboostub (pid 40237)")
    assert "8085" in str(err.value)
    assert "bamboostub (pid 40237)" in str(err.value)


def test_port_holder_is_empty_when_lsof_is_missing():
    def no_lsof(*args, **kwargs):
        raise FileNotFoundError("lsof")
    assert start_lab.port_holder(8085, run_out=no_lsof) == ""


def test_port_holder_asks_lsof_for_the_listener():
    calls = []
    def fake(*args, **kwargs):
        calls.append(args)
        return LSOF
    assert start_lab.port_holder(8085, run_out=fake) == "bamboostub (pid 40237)"
    assert calls == [("lsof", "-nP", "-iTCP:8085", "-sTCP:LISTEN", "-Fpc")]


def test_a_half_forwarded_pair_without_a_known_holder_keeps_the_old_message():
    with pytest.raises(LabError, match="port 8085 is already in use"):
        start_lab.decide_forward({8085: True, 54663: False}, holder=lambda port: "")
