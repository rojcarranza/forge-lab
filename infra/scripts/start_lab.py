#!/usr/bin/env python3
"""Bring the whole local lab up in one terminal: ports forwarded, agent running.

`make up` installs the stack but leaves two long-lived foreground processes to
start by hand — the port-forward (`make ui`) and the host agent (`make agent-run`)
— in that order, in two terminals. This runs both from one, owning the ordering
(the agent cannot register before 54663 answers) and the cleanup (the forward
dies with the agent instead of outliving it as an orphan holding port 8085).
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "bamboo-specs" / "src" / "main" / "java" / "lab" / "shared" / "python"
    ),
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import install_agent  # noqa: E402
import run_agent  # noqa: E402
from forgelab import proc  # noqa: E402

NAMESPACE = os.environ.get("BAMBOO_NAMESPACE", "ci")
POD = os.environ.get("BAMBOO_POD", "bamboo-0")
STATEFULSET = os.environ.get("BAMBOO_STATEFULSET", "bamboo")
UI_PORT = 8085
BROKER_PORT = 54663
PORTS = (UI_PORT, BROKER_PORT)
ROLLOUT_TIMEOUT = "600s"
# Generous: the forward only has to bind locally, but kubectl waits on the API
# server and on the pod being ready enough to dial.
FORWARD_TIMEOUT_SECONDS = 90
PROBE_TIMEOUT_SECONDS = 1.0
FORWARD_STOP_TIMEOUT_SECONDS = 5


def port_open(port: int, host: str = "localhost") -> bool:
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def decide_forward(states: dict) -> str:
    """Reuse a `make ui` already running in another terminal, or start our own.

    A half-open pair is neither: something else holds one of the ports, and a
    second forward would fail to bind it. Say which port rather than letting
    kubectl's "address already in use" arrive later, mid-startup.
    """
    if all(states.values()):
        return "reuse"
    if not any(states.values()):
        return "start"
    taken = ", ".join(str(port) for port, is_open in states.items() if is_open)
    proc.die(
        f"port {taken} is already in use but the rest of the forward is not — "
        "stop whatever holds it (a dead 'make ui'?) and retry"
    )


def wait_for_ports(ports, timeout=FORWARD_TIMEOUT_SECONDS, probe=port_open,
                   sleep=time.sleep, now=time.monotonic) -> bool:
    deadline = now() + timeout
    while now() <= deadline:
        if all(probe(port) for port in ports):
            return True
        sleep(1)
    return False


def needs_install(jar: Path) -> bool:
    """A zero-byte jar is a failed `kubectl cp`, not an install."""
    return not jar.is_file() or jar.stat().st_size == 0


def start_forward() -> subprocess.Popen:
    return subprocess.Popen([
        "kubectl", "-n", NAMESPACE, "port-forward", f"pod/{POD}",
        f"{UI_PORT}:{UI_PORT}", f"{BROKER_PORT}:{BROKER_PORT}",
    ])


def stop_forward(forward: subprocess.Popen):
    if forward.poll() is not None:
        return
    forward.terminate()
    try:
        forward.wait(timeout=FORWARD_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        forward.kill()


def main(argv):
    proc.require_tools("kubectl", "java")
    proc.run(
        "kubectl", "-n", NAMESPACE, "rollout", "status",
        f"statefulset/{STATEFULSET}", f"--timeout={ROLLOUT_TIMEOUT}",
    )

    forward = None
    if decide_forward({port: port_open(port) for port in PORTS}) == "reuse":
        print(f"Ports {UI_PORT} and {BROKER_PORT} already forwarded — reusing them.")
    else:
        forward = start_forward()
    try:
        if not wait_for_ports(PORTS):
            proc.die(
                f"port-forward never came up on {UI_PORT}/{BROKER_PORT} "
                f"after {FORWARD_TIMEOUT_SECONDS}s"
            )
        print(f"Bamboo UI: http://localhost:{UI_PORT} (admin/admin)")

        if needs_install(install_agent.AGENT_DIR / "agent-installer.jar"):
            print("No agent installed yet — installing from the server pod ...")
            install_agent.main([])

        run_agent.prepare()
        print("Starting the host agent (Ctrl-C stops the agent and the forward).")
        print("First run only: approve it once in Administration > Agents.")
        # A child, not execvp as `make agent-run` does: this process has to
        # outlive the JVM by long enough to take the port-forward down with it.
        proc.run(*run_agent.agent_argv(), check=False)
    finally:
        if forward is not None:
            stop_forward(forward)


if __name__ == "__main__":
    proc.main(main)
