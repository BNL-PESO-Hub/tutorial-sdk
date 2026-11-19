# Development

This guide covers editable development installation, unit and live-daemon
contract test suites, linting and documentation builds, and CI workflows.

---

## Installation

To install in editable development mode (for contributing or local development):

```bash
# Clone the repository
git clone https://github.com/bnl-peso-hub/tutorial-sdk.git
cd tutorial-sdk

# Install in development mode with test and formatting extras
# (and docker-py, for the docker-sdk backend)
SETUPTOOLS_SCM_PRETEND_VERSION=0.1.0 pip install -e ".[dev,docker]"

# Documentation tools
pip install -e ".[docs]"
```

> **Note:** `tutorial-sdk` uses [setuptools-scm](https://github.com/pypa/setuptools-scm) to derive its version from git tags. On a fresh clone without a release tag, `setuptools-scm` cannot determine the version automatically. Setting `SETUPTOOLS_SCM_PRETEND_VERSION` provides a fallback version for the local install.

| Extra | Installs |
| :--- | :--- |
| `docker` | docker-py, for the `docker-sdk` backend |
| `dev` | `pytest`, `pytest-cov`, `ruff` |
| `docs` | MkDocs, Material for MkDocs, mkdocstrings |

---

## Unit Tests

```bash
pytest
```

The unit suite needs no Docker daemon. Container operations are tested against
`tests/mock_backend.py`, an in-memory `ContainerBackend` that records every
call; the backends themselves are tested with mocked `subprocess` calls and a
mocked docker-py client.

Run it on a base install too (without the `docker` extra), as CI does: the
docker-py backend's unit module is then skipped and everything else must pass.

```bash
pip install -e ".[dev]"   # in a fresh environment without docker-py
pytest
```

`tests/test_backend_boundary.py` audits the sources: only
`backend/docker_cli.py` may build `docker` commands, and only
`backend/docker_sdk.py` may import docker-py. To exercise the SDK without
Docker in your own tests, pass a `ContainerBackend` instance through
`backend=` (for example `TutorialProject.build(backend=...)`).

---

## Contract Tests (Live Docker Daemon)

The contract suite runs the same cases against both backends on a real daemon,
so they stay behaviorally equivalent. It is opt-in through the `docker` pytest
marker:

```bash
pytest -m docker            # contract suite only
pytest -m "docker or not docker"   # everything
```

Backends that cannot be used on the host are skipped, not failed. The test
images are built from `busybox:1.36` and need no network once that image is
local. Push cases need a registry: they use `TUTORIAL_SDK_CONTRACT_REGISTRY`
when set, otherwise they start a `registry:2` container, and they are skipped
when neither works.

| Variable | Purpose | Default |
| :--- | :--- | :--- |
| `TUTORIAL_SDK_CONTRACT_BASE_IMAGE` | Base image of the test images | `busybox:1.36` |
| `TUTORIAL_SDK_CONTRACT_REGISTRY` | `host:port` of a registry for push cases | unset (start one) |
| `TUTORIAL_SDK_CONTRACT_REGISTRY_IMAGE` | Image of the registry started for push cases | `registry:2` |

The interrupt cases deliver Ctrl-C to a process group and run a `--shell`
case under a pseudo-terminal, so run the suite on Linux or macOS. After a run,
no container labelled `org.bnl-peso-hub.tutorial-sdk.managed=true` should be
left:

```bash
docker ps -aq --filter label=org.bnl-peso-hub.tutorial-sdk.managed=true
```

---

## Linting and Documentation

```bash
ruff check src tests
ruff format --check src tests
mkdocs build --strict      # warnings, broken links, and anchors fail the build
mkdocs serve               # preview on http://127.0.0.1:8000
```

The API reference pages are generated from docstrings (Google style) by
mkdocstrings, so a docstring change shows up in the docs.

---

## CI Workflows

`.github/workflows/tests.yml` runs on pushes to `main` and on pull requests:

<div class="wrap-middle" markdown>

| Job | What it checks | Reproduce locally |
| :--- | :--- | :--- |
| `lint` | `ruff check` and `ruff format --check` | the lint commands above |
| `docs` | `mkdocs build --strict` | `mkdocs build --strict` |
| `unit` | Python 3.10–3.13, each with a base install (docker-py must be absent) and with the `docker` extra | `pytest` in both environments |
| `contract` | `pytest -m docker` on `ubuntu-latest` against a `registry:2` service (`TUTORIAL_SDK_CONTRACT_REGISTRY=localhost:5000`), after a `tutorial-sdk doctor` step; fails if any container with the managed label remains after the test run | `pytest -m docker` |

</div>

`docs.yml` deploys the documentation to GitHub Pages from `main` (built with
`--strict`); `release.yml` creates a release tag, and `publish.yml` builds the
distribution and publishes it to PyPI.

User-facing changes are recorded in
[`CHANGELOG.md`](https://github.com/BNL-PESO-Hub/tutorial-sdk/blob/main/CHANGELOG.md).
