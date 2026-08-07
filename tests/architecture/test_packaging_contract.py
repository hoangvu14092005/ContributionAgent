"""Packaging, license, Docker and active-architecture contracts."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_project_license_and_dependency_contract_are_consistent() -> None:
    project = _project()
    dependencies = project["dependencies"]

    assert project["version"] == "4.1.0"
    assert project["license"]["text"] == "MIT"
    assert len(dependencies) == len(set(dependencies))
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    assert "License :: OSI Approved :: MIT License" in project["classifiers"]


def test_optional_engine_extras_do_not_change_default_runtime() -> None:
    optional = _project()["optional-dependencies"]

    assert "engine-mini-swe" in optional
    assert "engine-openhands" in optional
    assert optional["engine-mini-swe"]
    assert optional["engine-openhands"]


def test_docker_build_context_and_entrypoint_are_complete() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY LICENSE ." in dockerfile
    assert 'ENTRYPOINT ["contribai"]' in dockerfile
    assert "DOCKER_HOST" not in dockerfile
    assert "/var/run/docker.sock" not in dockerfile


def test_active_architecture_document_is_the_control_plane() -> None:
    active = (ROOT / "docs/CONTRIBUTION_CONTROL_PLANE.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    historical = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "GitHubPublisher" in active
    assert "EngineOutcome" in active
    assert "CONTRIBUTION_CONTROL_PLANE.md" in readme
    assert "historical reference" in historical.lower()
