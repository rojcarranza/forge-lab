#!/usr/bin/env python3
"""Run the host-local Bamboo agent in console mode."""

import os
import sys
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "bamboo-specs" / "src" / "main" / "java" / "lab" / "shared" / "python"
    ),
)

from forgelab import paths, proc  # noqa: E402

BAMBOO_URL = os.environ.get("BAMBOO_URL", "http://localhost:8085")
AGENT_HOME = Path(
    os.environ.get("AGENT_HOME", Path.home() / ".forgelab" / "bamboo-agent-home")
)
AGENT_DIR = Path.home() / ".forgelab" / "agent"

# The clone this agent was started from — the one durable checkout on the host.
# Bamboo's own per-plan checkouts are throwaway, so the plans write the cluster
# registry here instead (see forgelab.paths.REGISTRY_DIR).
CLONE_ROOT = Path(__file__).resolve().parents[2]

# Capabilities this host agent must offer for the provisioning plans to run.
REQUIRED_TOOLS = ("terraform", "ansible-playbook", "multipass", "java")

CAPABILITY_LINE = "agent.role=host"


def seed_capability(caps_file: Path):
    """Advertise agent.role=host so the multipass provisioning plans (which
    require it) only ever schedule here. Without it Bamboo is free to send them
    to the containerized k8s agent, which has no terraform/multipass and fails
    with "ERROR: missing tool: terraform". Mirrors the k8s agent's agent.role=ci.
    """
    caps_file.parent.mkdir(parents=True, exist_ok=True)
    existing = caps_file.read_text().splitlines() if caps_file.is_file() else []
    if CAPABILITY_LINE in existing:
        return
    with caps_file.open("a") as handle:
        handle.write(f"{CAPABILITY_LINE}\n")
    print(f"Seeded {CAPABILITY_LINE} in {caps_file}")


def registry_dir_env(env: dict) -> dict:
    """Hand every job this agent runs one registry location, unless already set.

    Jobs inherit this process's environment, which is one way both PROV and
    DEPROV — separate checkouts — can agree on where the cluster info files live.
    """
    if env.get("FORGELAB_REGISTRY_DIR"):
        return env
    return {**env, "FORGELAB_REGISTRY_DIR": str(CLONE_ROOT / "cluster_registered")}


def seed_registry_pointer(pointer: Path, registry: str):
    """Record the registry location on the host, not just in this agent's env.

    An agent started before this file existed, or by hand, still resolves the
    right directory — forgelab.paths reads the pointer when the variable is
    absent. Without it the registry silently depends on how the JVM was launched.
    """
    pointer.parent.mkdir(parents=True, exist_ok=True)
    if pointer.is_file() and pointer.read_text().strip() == registry:
        return
    pointer.write_text(f"{registry}\n")
    print(f"Seeded cluster registry location in {pointer}")


def prepare():
    """Everything the agent needs in place before the JVM starts.

    Split from main() so start_lab.py can run the agent as a child process
    without duplicating the capability and registry handoff.
    """
    proc.require_tools(*REQUIRED_TOOLS)
    seed_capability(AGENT_HOME / "bin" / "bamboo-capabilities.properties")
    os.environ.update(registry_dir_env(dict(os.environ)))
    seed_registry_pointer(paths.REGISTRY_POINTER, os.environ["FORGELAB_REGISTRY_DIR"])
    print(f"Cluster registry: {os.environ['FORGELAB_REGISTRY_DIR']}")


def agent_argv() -> list:
    """The console-mode agent command.

    Agent home is set via the -Dbamboo.home property (before -jar), not a flag.
    """
    return [
        "java",
        f"-Dbamboo.home={AGENT_HOME}",
        "-jar", str(AGENT_DIR / "agent-installer.jar"),
        f"{BAMBOO_URL}/agentServer/", "console",
    ]


def main(argv):
    prepare()
    # exec so the JVM replaces this process — no python wrapper left holding on
    # to a long-lived agent's signals.
    os.execvp("java", agent_argv())


if __name__ == "__main__":
    proc.main(main)
