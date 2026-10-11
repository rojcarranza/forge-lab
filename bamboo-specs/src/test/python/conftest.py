"""Put the lab's Python trees on sys.path the same way the entrypoints do."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
LAB = REPO_ROOT / "bamboo-specs" / "src" / "main" / "java" / "lab"

for path in (
    LAB / "shared" / "python",
    LAB / "provisioncluster" / "scripts",
    LAB / "deprovisioncluster" / "scripts",
    LAB / "specspublish" / "scripts",
    LAB / "bamlab" / "scripts",
    REPO_ROOT / "infra" / "scripts",
    REPO_ROOT / "infra" / "agent",
    REPO_ROOT / "infra" / "githooks",
):
    sys.path.insert(0, str(path))

import pytest  # noqa: E402


@pytest.fixture
def configs_dir(tmp_path, monkeypatch):
    """An empty stand-in for cluster_configs/, for anything that resolves a
    cluster's config file."""
    from forgelab import paths

    monkeypatch.setattr(paths, "CLUSTER_CONFIGS_DIR", tmp_path)
    return tmp_path
