"""Source audit: ``docker`` is only ever invoked by the CLI backend.

Every ``docker`` subprocess command in the package is built in
``backend/docker_cli.py``; nothing else spawns the Docker CLI or
imports docker-py except ``backend/docker_sdk.py``.
"""

import ast
from pathlib import Path

import tutorial_sdk

PACKAGE = Path(tutorial_sdk.__file__).parent
CLI_BACKEND = PACKAGE / "backend" / "docker_cli.py"
SDK_BACKEND = PACKAGE / "backend" / "docker_sdk.py"


def _modules():
    return sorted(PACKAGE.rglob("*.py"))


def _docker_commands(tree):
    """Yield list/tuple literals whose first element is ``"docker"``."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "docker":
                yield node


def test_docker_commands_only_in_cli_backend():
    offenders = [
        f"{path.relative_to(PACKAGE)}:{node.lineno}"
        for path in _modules()
        if path != CLI_BACKEND
        for node in _docker_commands(ast.parse(path.read_text()))
    ]
    assert offenders == []


def test_docker_py_only_in_sdk_backend():
    offenders = []
    for path in _modules():
        if path == SDK_BACKEND:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            if any(n == "docker" or n.startswith("docker.") for n in names):
                offenders.append(f"{path.relative_to(PACKAGE)}:{node.lineno}")
    assert offenders == []
