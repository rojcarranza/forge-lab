"""Wait for freshly launched VMs to answer on SSH before ansible runs.

`multipass launch` returns once the instance is Running and holds a DHCP lease,
which is not the same as the host being able to reach it: on macOS the vmnet
bridge's ARP entry for a new VM can still be missing when the launch of the
last VM of a batch returns, and every address on the subnet then fails with
EHOSTUNREACH. ansible reports that as `UNREACHABLE! ... No route to host` on
every host at once, seconds into the play, and the whole provision run is lost
after the VMs were already built.

So the gate is here rather than in the playbook: a `wait_for_connection`
pre_task would run per host inside the same play that then fails, and
`make addons` against a stopped-and-started cluster needs the same wait.
"""

from __future__ import annotations

import socket
import time

from .proc import die

SSH_PORT = 22
ATTEMPTS = 60
INTERVAL_SECONDS = 5
# Short: an unreachable host on a local bridge fails immediately with
# EHOSTUNREACH, and this bound only matters for a host that black-holes.
CONNECT_TIMEOUT_SECONDS = 3


def port_open(ip: str, port: int = SSH_PORT, timeout: float = CONNECT_TIMEOUT_SECONDS) -> bool:
    """True when a TCP connection to ip:port completes. Never raises."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def pending(hosts, probe=None, attempts=None, interval=None, sleep=time.sleep) -> list:
    """[(name, ip)] still not answering after `attempts` passes.

    A host that answers drops out of the next pass, so a settled cluster costs
    one probe per host and a straggler never pays for its neighbours.

    `probe`, `attempts` and `interval` default to the module's own names at
    CALL time, not at import time — a default bound at def time would freeze
    the original `port_open`, so a caller substituting one would be ignored.
    """
    probe = port_open if probe is None else probe
    attempts = ATTEMPTS if attempts is None else attempts
    interval = INTERVAL_SECONDS if interval is None else interval
    remaining = list(hosts)
    for attempt in range(attempts):
        remaining = [(name, ip) for name, ip in remaining if not probe(ip)]
        if not remaining:
            return []
        if attempt < attempts - 1:
            sleep(interval)
    return remaining


def wait_for_ssh(hosts, **kwargs):
    """Block until every host answers on SSH, or die naming the ones that did not."""
    attempts = kwargs.get("attempts") or ATTEMPTS
    interval = kwargs.get("interval") or INTERVAL_SECONDS
    print(
        f"==> waiting for ssh on {len(list(hosts))} node(s) "
        f"(timeout {attempts * interval}s)"
    )
    left = pending(hosts, **kwargs)
    if left:
        names = ", ".join(f"{name} ({ip})" for name, ip in left)
        die(
            f"no ssh on {names} within {attempts * interval}s — "
            "the VMs are up but the host cannot route to them"
        )
