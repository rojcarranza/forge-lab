#!/usr/bin/env python3
"""Run the install stage against an already-provisioned cluster's inventory.

Split out of provision.py so a role can be iterated on with `make addons`
instead of a full rebuild. provision.py imports `run` rather than shelling out,
so there is exactly one code path.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "python"))

from forgelab import (  # noqa: E402
    credentials, inventory, paths, planvars, proc, reachability,
)

USAGE = "usage: install.py <cluster_name> [cluster_config]"


def extra_vars(cluster_type: str, addons, report, secret_values: dict) -> dict:
    """The -e payload for site.yml. Pure — the caller writes it out."""
    return {
        "cluster_type": cluster_type,
        "addons": ",".join(addons),
        "component_report": str(report),
        **secret_values,
    }


def run(cluster: str, config):
    """Install everything the cluster asks for. Returns the component report path."""
    inv = paths.INV_DIR / f"{cluster}.ini"
    if not inv.is_file():
        proc.die(f"no inventory for {cluster} — provision it first")

    # Gate the play on the hosts actually being routable. Without it a
    # just-launched cluster fails every host with 'No route to host'
    # seconds in, throwing away VMs that were seconds from ready.
    reachability.wait_for_ssh(inventory.parse_hosts(inv.read_text()))

    technologies = config.enabled()
    secret_values = credentials.ensure(cluster, technologies)

    # mkdtemp is 0700, and the vars file is opened 0600 and deleted after the
    # run: passwords must never reach argv, which is world-readable in `ps`.
    workdir = Path(tempfile.mkdtemp(prefix="forgelab-"))
    report = workdir / "components.json"
    varsfile = workdir / "extra-vars.json"
    handle = os.open(varsfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w") as out:
        json.dump(
            extra_vars(config.cluster_type, technologies, report, secret_values), out
        )

    try:
        proc.run(
            "ansible-playbook",
            paths.SITE_YML,
            "-i", inv,
            "-e", f"@{varsfile}",
            env=paths.ansible_env(os.environ),
        )
    finally:
        varsfile.unlink(missing_ok=True)
    return report


def main(argv):
    cluster, config = planvars.resolve(
        argv[0] if argv else "", argv[1] if len(argv) > 1 else "", USAGE
    )
    run(cluster, config)


if __name__ == "__main__":
    proc.main(main)
