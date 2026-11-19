# Command Line Interface (CLI)

The command-line interface `tutorial-sdk` provides tutorial authors with an automated workflow to package, build, run, and validate containers.

---

## Choosing a Container Backend

Commands that use Docker — `build`, `validate --container`, `run`, and `ci` — go through a **container backend**:

* `docker-cli`: the `docker` command-line client (needs the `docker` binary on `PATH`);
* `docker-sdk`: docker-py and the Docker Engine API (needs `pip install 'tutorial-sdk[docker]'`);
* `auto` (default): `docker-sdk` when docker-py is installed and the Docker daemon answers, otherwise `docker-cli`.

The backend is taken from the first of these that names one:

1. `--backend {auto,docker-cli,docker-sdk}` on the command;
2. the `TUTORIAL_SDK_BACKEND` environment variable;
3. `build.backend` in `tutorial.yml` (see [Configuration Schema](config-schema.md#5-build));
4. `auto`.

`auto` (or no value) at any level defers to the next one, so `--backend auto` does not override `TUTORIAL_SDK_BACKEND`. An invalid `--backend` value is a usage error (exit code `2`); an invalid `TUTORIAL_SDK_BACKEND` value is a configuration error (exit code `2`; `doctor` reports it instead).

```bash
# Use the Docker CLI for this command only
tutorial-sdk build --backend docker-cli

# Use docker-py for every command in this shell
export TUTORIAL_SDK_BACKEND=docker-sdk
```

Run [`tutorial-sdk doctor`](#doctor) to see which backend a command would use, and why. See [Container Backends](architecture.md#container-backends) for what each backend does.

---

## `init`
Creates a new tutorial project. When used without flags, generates a **minimal** template (see [`scaffold`](#scaffold) command). When used with `--from`, `--url`, or `--github`, imports an existing project by auto-detecting notebooks, scripts, data files, documentation, and dependencies.

```bash
tutorial-sdk init [PATH]
tutorial-sdk init --from DIR [PATH]
tutorial-sdk init --url URL [PATH] [--remove-clone]
tutorial-sdk init --github ORG/REPO [PATH] [--remove-clone]
```

**Arguments**

* `PATH` (_optional_): Target directory for the new tutorial project. Defaults to `.` for plain `init`, or `{source}_tutorial` when importing.

**Flags**

* `--from DIR`: Import an existing local directory. Scans for Jupyter notebooks and auto-detects dependencies from notebook imports, install commands, requirements files, and `pyproject.toml` metadata. Dependencies are cross-referenced so authoritative package names take precedence over AST-derived import names. See [Dependency Detection](architecture.md#dependency-detection) for details.

* `--url URL`: Clone a remote Git repository and import it. Requires `git` on `PATH`. By default, the clone is kept for inspection next to the target directory.

* `--github ORG/REPO`: Shorthand for importing a GitHub repository (clones `https://github.com/ORG/REPO.git`).

* `--remove-clone` (_flag_, _optional_): Delete the cloned repository after importing. Only applies to `--url` and `--github`. Without this flag, the clone is preserved for inspection.

**Discovery rules**

Scripts (`.py`), data files (`.csv`, `.json`, `.parquet`, etc.), and documentation (`.md`, `.rst`) are discovered **within the directories containing notebooks** and their subdirectories. Top-level `README.md` is always included.

If a `tutorial.yml` already exists in the target directory, it is validated and a status message is printed - no files are overwritten.

**Python version** is detected from `.python-version` or `pyproject.toml` `requires-python` (defaults to `3.11`).

**Examples**

```bash
# Import a local project directory
tutorial-sdk init --from ./my-notebooks

# Import into a specific target directory
tutorial-sdk init --from ./my-notebooks ./packaged-tutorial

# Import from a GitHub repository
tutorial-sdk init --github google/applied-ml

# Import from any Git URL
tutorial-sdk init --url https://gitlab.com/org/repo.git
```

## `inspect`
Parses, resolves, and prints the current tutorial metadata, contents, system packages, and dependencies in a clean, structured JSON stream.

```bash
tutorial-sdk inspect [--config CONFIG]
```

**Arguments**

* `--config CONFIG` (_optional_): Path to the YAML specification. Defaults to `./tutorial.yml`.

---

## `add`
Adds content paths or dependencies to the `tutorial.yml` specification file without requiring manual edits.

**Content types**

```bash
tutorial-sdk add notebook [--config CONFIG] PATHS...
tutorial-sdk add script [--config CONFIG] PATHS...
tutorial-sdk add data [--config CONFIG] PATHS...
tutorial-sdk add doc [--config CONFIG] PATHS...
tutorial-sdk add exercise [--config CONFIG] PATHS...
tutorial-sdk add solution [--config CONFIG] PATHS...
```

**Dependencies**

```bash
tutorial-sdk add dependency [--config CONFIG] PACKAGES...
tutorial-sdk add apt [--config CONFIG] PACKAGES...
```

**Arguments**

* `PATHS` / `PACKAGES`: One or more paths or package names to add.
* `--config CONFIG` (_optional_): Path to the YAML specification. Defaults to `./tutorial.yml`.

**Examples**

```bash
tutorial-sdk add notebook notebooks/lesson1.ipynb
tutorial-sdk add dependency numpy pandas matplotlib
tutorial-sdk add apt git build-essential
```

---

## `generate`
Compiles and writes declarative build templates to disk.

```bash
tutorial-sdk generate dockerfile [--config CONFIG] \
                                 [--output OUTPUT]
```

**Arguments**

* `--config CONFIG` (_optional_): Path to the YAML specification. Defaults to `./tutorial.yml`.
* `--output OUTPUT` (_optional_): Target path to save the generated Dockerfile. Defaults to `build.dockerfile` from the configuration.

---

## `build`
Runs the build pipeline: generates the `Dockerfile`, writes configured
reproducibility and devcontainer artifacts, and compiles the final container
image through the selected [container backend](#choosing-a-container-backend).

```bash
tutorial-sdk build [--config CONFIG] \
                   [--image IMAGE] \
                   [--platform PLATFORM] \
                   [--build-output] \
                   [--backend {auto,docker-cli,docker-sdk}]
```

**Arguments**

* `--config CONFIG` (_optional_): Path to the YAML specification. Defaults to `./tutorial.yml`.
* `--image IMAGE` (_optional_): Sets the target image override for the resulting container image. Defaults to `[name]:latest` derived from configuration.
* `--platform PLATFORM` (_optional_): Sets the target platform for the resulting container image.
* `--build-output` (_flag_, _optional_): Print the image build output as it is produced. Without it the build is silent, and a failed build prints its output in the `Build error:...` message; with it the output has already been shown, so the error message is short. Defaults to `false`.
* `--backend {auto,docker-cli,docker-sdk}` (_optional_): Container backend to use; overrides `TUTORIAL_SDK_BACKEND` and `build.backend`. See [Choosing a Container Backend](#choosing-a-container-backend).

The build output format depends on the backend: the docker-py backend uses Docker's classic builder (`Step 1/4 :...`), the Docker CLI typically uses BuildKit (`#1 [internal] ...`). With the docker-py backend, Dockerfiles that need BuildKit are built through the Docker CLI when it is installed (see [Native vs. Delegated `docker-sdk` Operations](architecture.md#native-vs-delegated-docker-sdk-operations)).

---

## `run`
Launches the built tutorial image locally in the foreground. Automatically maps and binds the exposed container runtime ports.

```bash
tutorial-sdk run [--config CONFIG] \
                 [--image IMAGE] \
                 [--port PORT] \
                 [--shell] \
                 [--backend {auto,docker-cli,docker-sdk}]
```

**Arguments**

* `--config CONFIG` (_optional_): Path to the YAML specification. Defaults to `./tutorial.yml`.
* `--image IMAGE` (_optional_): Override target container image to run.
* `--port PORT` (_optional_): Host port to bind the container. Defaults to `8888`.
* `--shell` (_flag_, _optional_): Directs the launcher to boot into a standard shell (`/bin/sh`) inside the container instead of starting JupyterLab. Always runs through the Docker CLI, so it needs the `docker` binary with either backend. Defaults to `false`.
* `--backend {auto,docker-cli,docker-sdk}` (_optional_): Container backend to use; overrides `TUTORIAL_SDK_BACKEND` and `build.backend`.

**Stopping the container**

The container is removed when it exits, including after an interrupt. Both backends apply the same stop policy:

1. The first Ctrl-C prints `Stopping container (interrupt again to force)...` and asks the container to stop.
2. If the container is still running 10 seconds later, or when Ctrl-C is pressed again, it is killed and removed.

With `docker-cli`, the run then ends with a `KeyboardInterrupt`, as before. If the container cannot be removed, a `Warning: could not remove container...` (or `Warning: could not identify the container...`) line is printed; `tutorial-sdk doctor` lists such containers. With `docker-sdk`, SIGTERM is handled like Ctrl-C, and the command exits with the container's exit code instead of a traceback (`--shell` runs through the Docker CLI and behaves as with `docker-cli`). In an interactive `--shell`, Ctrl-C goes to the container's shell; leave it with `exit`.

**Exit code**

The container's exit code; `125` when the Docker daemon rejects the run before the container starts (for example, the port is already allocated), as with `docker run`. With `docker-cli` (and for `--shell`), errors reported by the `docker` client itself are passed through with the client's exit code and message: for example, `1` when it cannot connect to the Docker daemon. See [Exit Codes](#exit-codes) for errors raised before the container runs.

The container carries the label `org.bnl-peso-hub.tutorial-sdk.managed=true` (see [Managed-Container Label](architecture.md#managed-container-label)).

---

## `validate`
Performs validation checks to ensure spec consistency, content file existence, import availability, and notebook execution hygiene.

```bash
tutorial-sdk validate [--config CONFIG] \
                      [--strict] \
                      [--container] \
                      [--output OUTPUT] \
                      [--backend {auto,docker-cli,docker-sdk}]
```

**Arguments**

* `--config CONFIG` (_optional_): Path to the YAML specification. Defaults to `./tutorial.yml`.
* `--strict` (_flag_, _optional_): When enabled, fails validation checks (exiting with non-zero code) on warnings. Defaults to `false`.
* `--container` (_flag_, _optional_): Enables live container validation against the configured or default image tag. This command does not build the image first; use `tutorial-sdk build` or `tutorial-sdk ci --mode test` when the image must be built before validation. See [Live Container Validation](architecture.md#live-container-validation). Defaults to `false`.
* `--output OUTPUT` (_optional_): Specifies the path to save the resulting JSON validation report. Defaults to `tutorial-validation.json`.
* `--backend {auto,docker-cli,docker-sdk}` (_optional_): Container backend for `--container`; overrides `TUTORIAL_SDK_BACKEND` and `build.backend`. Without `--container` no backend is used.

**Exit code**

`0` when all checks pass, `1` otherwise.

---

## `scaffold`
Creates a pre-configured tutorial directory layout from a built-in skeleton template.

```bash
tutorial-sdk scaffold TEMPLATE --name NAME [--path PATH]
```

**Arguments**

* `TEMPLATE`: The template style.
    - `minimal`: Minimal template.
    - `notebook-tutorial`: Notebook-centric tutorial.
    - `workshop`: Workshop-style tutorial.
    - `lab-exercise`: Lab exercise-style tutorial.
    - `demo`: Demo-style tutorial.
* `--name NAME`: The name of the new tutorial project.
* `--path PATH` (_optional_): Target folder path. Defaults to the provided project name.

---

## `ci`
Spawns thin, highly-optimized workflows built specifically for GitHub Actions integration. Runs validation, compiles Docker environments, tests live containers, and produces build reports and execution logs.

```bash
tutorial-sdk ci [--mode {validate,build,test,publish,all}] \
                [--config CONFIG] \
                [--dockerfile DOCKERFILE] \
                [--image IMAGE] \
                [--push] \
                [--strict] \
                [--no-cache] \
                [--build-output] \
                [--backend {auto,docker-cli,docker-sdk}]
```

**Arguments**

* `--mode {validate,build,test,publish,all}` (_optional_): Specifies the CI run mode. Any other value is a command-line error (exit code `2`).
    - `validate` (_default_): Runs static validation only (generates `Dockerfile` and `tutorial-manifest.json`). No container backend is selected.
    - `build`: Builds the container image (generates `Dockerfile` and manifests internally).
    - `test`: Builds the container image, then runs live container validation inside the built image.
    - `publish`: Builds the container image and pushes to a registry (requires `--push`).
    - `all`: Combines build, live container validation, and optional push.
* `--config CONFIG` (_optional_): Path to `tutorial.yml`. Defaults to `./tutorial.yml`.
* `--dockerfile DOCKERFILE` (_optional_): Target generated Dockerfile path. Defaults to `./Dockerfile`.
* `--image IMAGE` (_optional_): Target tag name for the build image.
* `--push` (_flag_, _optional_): Whether to push the image to a container registry after building. Only applies to `publish` and `all` modes. Defaults to `false`.
* `--strict` (_flag_, _optional_): Whether to fail validation checks on warnings. Defaults to `false`.
* `--no-cache` (_flag_, _optional_): Disables Docker layer caching. Defaults to `false` (caching is enabled by default, whatever `build.cache` says).
* `--build-output` (_flag_, _optional_): Print the image build output as it is produced; the lines are also written to `tutorial-sdk.log`. Defaults to `false`.
* `--backend {auto,docker-cli,docker-sdk}` (_optional_): Container backend to use; overrides `TUTORIAL_SDK_BACKEND` and `build.backend`.

**Backend and push behavior**

In the `build`, `test`, `publish`, and `all` modes, the backend is selected once, when the build starts, and the same backend builds, validates, and pushes the image. If no backend is available, the run logs `Build failed:...`, writes `tutorial-build.json` with `"status": "failure"`, and exits with `3`.

A failed push is logged as `Push failed: <registry or daemon message>` and does not fail the run: `tutorial-build.json` then reports `"pushed": false` and `"digest": null`. Registry credentials come from the Docker configuration (`docker login`) with either backend; a tag without a version is pushed as `:latest`.

---

## `doctor`
Reports the container backends available on this host: whether each one is usable, the docker-py / Docker CLI and Docker Engine versions, the daemon endpoint each backend talks to and where it comes from, which backend a command would select (and which rule decided), and the containers carrying tutorial-sdk's managed-container label. It changes nothing: no container is started and nothing is built or removed (for `docker-sdk`, the classic-builder probe sends a `FROM scratch` build request, which the builder rejects). Include its output in bug reports.

```bash
tutorial-sdk doctor [--config CONFIG] \
                    [--backend {auto,docker-cli,docker-sdk}] \
                    [--json]
```

**Arguments**

* `--config CONFIG` (_optional_): Tutorial config whose `build.backend` takes part in the selection. Defaults to `tutorial.yml`, which is ignored when it does not exist; a `--config` path that does not exist is a configuration error. A config that cannot be parsed is reported as a note, and its `build.backend` is ignored.
* `--backend {auto,docker-cli,docker-sdk}` (_optional_): Evaluate the selection as if `--backend` were given to another command.
* `--json` (_flag_, _optional_): Print the report as JSON instead of text. Defaults to `false`.

**Output**

```text
tutorial-sdk 0.2.0 | Python 3.12.7 | Linux x86_64
Config: tutorial.yml (build.backend: unset)

Backends
  docker-sdk  usable
      docker-py 7.2.0 | Docker Engine 29.9.1 (API 1.56)
      endpoint: unix:///var/run/docker.sock (default)
      classic builder (used for docker-py builds): served
  docker-cli  usable
      Docker CLI 29.9.1 | Docker Engine 29.9.1 (API 1.56)
      endpoint: unix:///var/run/docker.sock (context default)

Selection
  docker-sdk: auto: docker-py is installed and the daemon answered
  (precedence: --backend > TUTORIAL_SDK_BACKEND > build.backend > auto)

Containers labelled org.bnl-peso-hub.tutorial-sdk.managed=true
  3f2a9c1b7d4e  running   12m  my-tutorial:latest  eager_turing
  A listed container is not necessarily orphaned
  (remove one with `docker rm -f <id>`).
```

* **Backends**: `docker-sdk` is *usable* when docker-py is installed and the daemon answers a ping; `docker-cli` when the `docker` binary is on `PATH` (its daemon may still be unreachable, which is then reported). The endpoint source is `DOCKER_HOST`, `context <name>` (the Docker CLI always names its context, `context default` when none is set), or `default` (docker-py's default socket). For `docker-sdk`, the report also says whether the daemon still serves the classic builder that docker-py builds use; if it reports `NOT served`, native builds fail, so use `--backend docker-cli` for builds.
* **Selection**: the backend a command would use with the same `--backend`, `TUTORIAL_SDK_BACKEND`, and `build.backend` values, the rule that decided, and why it is (un)available. An invalid `TUTORIAL_SDK_BACKEND` value is reported here.
* **Containers**: containers started by `tutorial-sdk run` (see [Managed-Container Label](architecture.md#managed-container-label)), with state and age. A listed container may still be in use by a running `tutorial-sdk run`; nothing is removed.

**Exit code**

`0` when the selected backend is usable and reaches its Docker daemon, `1` otherwise.

---

## Exit Codes

All commands report failures with these exit codes, so scripts and GitHub Actions can route step failures accurately:

| Exit Code | Reason | Description |
| :---: | :--- | :-- |
| `0` | **Success** | The command completed cleanly. |
| `1` | **Validation Failure** | One or more validation checks failed (`validate`, `ci`); for `doctor`, the selected backend is unusable or cannot reach its Docker daemon (including an invalid `TUTORIAL_SDK_BACKEND` value, which `doctor` reports instead of exiting with `2`; an invalid `--backend` is still a command-line error, and an invalid `build.backend` is reported as a note and ignored). |
| `2` | **Configuration Error** | `tutorial.yml` cannot be read or parsed, a backend name is invalid, or the command line is invalid. |
| `3` | **Build Failure** | The image build failed, or no container backend is available for a build. |
| `4` | **Scaffold / Import Failure** | A scaffold template or import operation could not complete. |
| `5` | **Internal SDK Error** | Any other SDK error, for example no container backend available for `run` or `validate --container`. |

`run` exits with the container's exit code once the container has started; with `docker-cli`, errors from the `docker` client itself (for example, an unreachable daemon) keep the client's exit code (see [`run`](#run)).
