import pytest

from forgelab import reachability
from forgelab.proc import LabError

HOSTS = [
    ("lab1-management-1", "192.168.252.157"),
    ("lab1-compute-1", "192.168.252.162"),
]


def never(_ip, **_kwargs):
    return False


def always(_ip, **_kwargs):
    return True


def test_pending_is_empty_when_every_host_answers_immediately():
    assert reachability.pending(HOSTS, probe=always, sleep=lambda _s: None) == []


def test_pending_names_the_hosts_that_never_answered():
    left = reachability.pending(HOSTS, probe=never, attempts=2, sleep=lambda _s: None)
    assert left == HOSTS


def test_pending_stops_probing_a_host_once_it_answers():
    """The regression: a host that came up on pass 1 must not be re-probed on
    pass 2, or a slow cluster pays a connect timeout per settled node per pass."""
    seen = []

    def up_after_first_pass(ip, **_kwargs):
        seen.append(ip)
        return ip.endswith(".157")

    reachability.pending(
        HOSTS, probe=up_after_first_pass, attempts=2, sleep=lambda _s: None
    )
    assert seen.count("192.168.252.157") == 1
    assert seen.count("192.168.252.162") == 2


def test_pending_returns_as_soon_as_the_last_host_comes_up():
    passes = {"n": 0}

    def up_on_third_pass(_ip, **_kwargs):
        passes["n"] += 1
        return passes["n"] >= 3

    slept = []
    assert (
        reachability.pending(
            [HOSTS[0]], probe=up_on_third_pass, attempts=9, sleep=slept.append
        )
        == []
    )
    assert len(slept) == 2


def test_pending_does_not_sleep_after_the_final_attempt():
    slept = []
    reachability.pending(HOSTS, probe=never, attempts=3, sleep=slept.append)
    assert len(slept) == 2


def test_wait_for_ssh_dies_naming_the_unreachable_hosts():
    with pytest.raises(LabError, match=r"lab1-management-1 \(192\.168\.252\.157\)"):
        reachability.wait_for_ssh(
            HOSTS, probe=never, attempts=1, sleep=lambda _s: None
        )


def test_wait_for_ssh_returns_quietly_when_every_host_answers():
    assert reachability.wait_for_ssh(HOSTS, probe=always, sleep=lambda _s: None) is None


def test_port_open_is_false_for_an_address_with_no_listener():
    """127.0.0.1 on a port nothing binds refuses fast — the probe must report
    that as 'not up', never raise."""
    assert reachability.port_open("127.0.0.1", port=1, timeout=1) is False
