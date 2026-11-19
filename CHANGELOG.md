# Changelog

All notable changes to `tutorial-sdk` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-NN

Container operations now run through a pluggable backend layer supporting both
the Python Docker SDK (`docker-py`) and the Docker CLI. Existing public APIs
(`TutorialProject.build/validate/run`, `BuildResult`, `RunResult`,
`ValidationReport`), validation check names, and CLI workflows remain
backward-compatible.

### Added

- **Pluggable container backend layer** (`tutorial_sdk.backend`):
  `ContainerBackend` protocol, `DockerCliBackend` (`docker` CLI),
  `DockerSdkBackend` (`docker-py` / Docker Engine API), `get_backend()`,
  `preference_source()`, `BACKEND_CHOICES`, `BackendName`, result types
  (`ImageBuildResult`, `ImagePushResult`, `ContainerHandle`, `ExecResult`,
  `RunExit`), diagnostic types (`BackendInfo`, `ManagedContainer`), and backend
  exceptions (`BackendError`, `BackendUnavailableError`, `ImageBuildFailed`,
  `ImagePushFailed`, `ContainerStartFailed`, `CopyFailed`).
- **Optional `docker` extra** (`pip install 'tutorial-sdk[docker]'`): installs
  `docker-py` (`docker~=7.2`). With `auto` (the default), `docker-sdk` is
  preferred when `docker-py` is installed and the daemon responds to a ping,
  falling back to `docker-cli` otherwise.
- **Backend selection controls**: `--backend {auto,docker-cli,docker-sdk}` on
  `build`, `validate` (with `--container`), `run`, `ci`, and `doctor`; the
  `TUTORIAL_SDK_BACKEND` environment variable; and the optional `build.backend`
  field in `tutorial.yml` (unset defaults to `auto` and is omitted when writing
  `tutorial.yml`). Precedence: `--backend` → `TUTORIAL_SDK_BACKEND` →
  `build.backend` → `auto`.
- **Programmatic backend injection**: `backend=` keyword (accepting a backend
  name or a `ContainerBackend` instance) on
  `TutorialProject.build/validate/run`, `LocalBuilder.build`, and the
  `DockerBuilder`, `ContainerValidator`, and `RuntimeLauncher` constructors.
- **Environment diagnostics (`tutorial-sdk doctor`)**: new CLI command and
  `tutorial_sdk.doctor.diagnose()` API (`--config`, `--backend`, `--json`)
  reporting backend usability, active backend selection and deciding rule,
  `docker-py` / Docker CLI and Engine versions, daemon endpoints and sources
  (`DOCKER_HOST`, Docker context, or default socket), classic-builder
  availability for `docker-sdk`, and containers carrying the managed-container
  label.
- **Live build output streaming (`--build-output`)**: opt-in `--build-output`
  flag on `build` and `ci` (which also writes build lines to
  `tutorial-sdk.log`), backed by the `log_callback=` keyword on
  `TutorialProject.build`, `LocalBuilder.build`, and `DockerBuilder.build`.
- **Managed-container ownership label**: foreground containers started by
  `tutorial-sdk run` on both backends carry
  `org.bnl-peso-hub.tutorial-sdk.managed=true` (`MANAGED_LABEL`).
- **Docker context endpoint discovery for `docker-sdk`**: when `DOCKER_HOST` is
  unset and the default socket does not answer, `DockerSdkBackend` inspects
  `DOCKER_CONTEXT` or `currentContext` in the Docker config so Docker Desktop,
  Colima, and OrbStack work out of the box.
- **BuildKit feature detection (`requires_buildkit`)**: `DockerSdkBackend`
  detects BuildKit-only Dockerfile features (parser directives, `RUN --mount`
  and related flags, heredocs, automatic platform args) and routes those builds
  through the Docker CLI when installed. Native `docker-sdk` builds populate
  `BuildResult.digest` with the image ID.
- **CI test workflow and documentation suite**: `.github/workflows/tests.yml`
  (ruff lint/format, `mkdocs build --strict`, unit matrix across Python
  3.10–3.14 × base/`[docker]`, and live-daemon contract suite `pytest -m docker`
  against both backends), `tests/test_docs_reference.py`, and new
  Troubleshooting and Development documentation pages alongside updated
  Architecture, CLI, Configuration Schema, and API reference pages.
- **Spec-level image resolution helpers**: `TutorialSpec.effective_image` and
  `TutorialSpec.resolve_image()`, plus `ResolutionResult.missing_relative_paths`
  and optional `image=` override support on `LocalBuilder.prepare()` and
  `LocalBuilder.write_manifest()`.

### Changed

- **Container validation startup polling**: `ContainerValidator` now polls
  container state over a 2-second observation window (`wait_running` /
  `observe_running`) instead of a fixed `time.sleep(2)`, failing immediately if
  the container exits early.
- **Foreground run signal handling (`tutorial-sdk run`)**: on both backends,
  the first Ctrl-C (`SIGINT`, plus `SIGTERM` on `docker-sdk`) prints
  `Stopping container (interrupt again to force)...` and gives the container
  10 seconds (`STOP_GRACE`) to stop before force-killing/removing it (or
  immediately on a second interrupt). `docker-sdk` exits with the container's
  exit code instead of raising a `KeyboardInterrupt` traceback, and daemon
  startup errors return exit code `125`. Interactive `--shell` runs always
  delegate to the Docker CLI.
- **Unified backend per `ci` run**: `tutorial-sdk ci` resolves the container
  backend once at the start of build modes (`build`, `test`, `publish`, `all`)
  and reuses it across build, container validation, and image push.
- **Structured backend unavailable errors**: missing Docker CLI or unreachable
  daemons now raise `BuildError`, `ValidationError`, or
  `BackendUnavailableError` instead of raw `FileNotFoundError` exceptions.
- **Strict documentation builds**: `.github/workflows/docs.yml` now deploys
  with `mkdocs gh-deploy --force --strict`, `mkdocs.yml` enables
  `validation.anchors: warn`, and the `docs` extra requires
  `mkdocstrings[python]>=1.0.7,<2.0` and `pymdown-extensions>=12.2,<13.0`
  (earlier mkdocstrings releases fail with pymdown-extensions 12).
- **Internal Docker subprocess boundary**: `DockerBuilder`,
  `ContainerValidator`, `RuntimeLauncher`, and `cli` no longer invoke
  `subprocess` directly; all `docker` CLI calls are isolated in
  `DockerCliBackend` (enforced by `tests/test_backend_boundary.py`). Tests and
  integrations should pass a `ContainerBackend` via `backend=` rather than
  monkeypatching `subprocess` in higher-level modules.
- **Default tutorial runtime Python version**: `runtime.python` and
  `build.base_image` now default to `"3.12"` and `"python:3.12-slim"` (was
  `"3.11"` / `"python:3.11-slim"`).
- **Resolution result naming and consolidation**: renamed
  `ResolvedTutorialProject` to `ResolutionResult` to match `BuildResult` and
  `RunResult` (avoiding confusion with `TutorialProject`). `AssetValidator` now
  delegates content path checking to `TutorialResolver`, and `DockerBuilder`,
  `RuntimeLauncher`, `ContainerValidator`, `TutorialSpec.to_manifest_dict()`,
  and `tutorial-sdk ci` share `TutorialSpec.resolve_image()` instead of
  duplicating fallback logic or scanning content files during `ci` builds.

### Fixed

- **Orphaned containers on interrupted CLI runs**: `tutorial-sdk run` with
  `docker-cli` now supervises `docker run` via `--cidfile` in a dedicated
  session and force-removes containers that ignore `SIGINT` after the 10-second
  grace period or a second Ctrl-C, while preserving `RunResult.command` and
  `KeyboardInterrupt` propagation.
- **`ci --mode` accepts only known modes**: `validate`, `build`, `test`,
  `publish`, or `all`. An unknown mode used to do nothing and exit `0`; it is
  now a command-line error (exit code `2`).
- **Consistent `image` field in `tutorial-manifest.json`**:
  `TutorialSpec.to_manifest_dict()` and `LocalBuilder.write_manifest()` now
  record the effective or overridden image tag (defaulting to `<name>:latest`
  when `build.image` is unset) instead of emitting `"image": null`, matching
  `TutorialProject.inspect()` and `tutorial-sdk ci`.

[0.2.0]: https://github.com/BNL-PESO-Hub/tutorial-sdk/compare/v0.1.2...v0.2.0
