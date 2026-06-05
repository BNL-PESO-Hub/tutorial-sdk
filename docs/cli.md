# Command Line Interface (CLI)

The command-line interface `tutorial-sdk` provides tutorial authors with an automated workflow to package, build, run, and validate containers.

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
tutorial-sdk add notebook PATHS...
tutorial-sdk add script PATHS...
tutorial-sdk add data PATHS...
tutorial-sdk add doc PATHS...
tutorial-sdk add exercise PATHS...
tutorial-sdk add solution PATHS...
```

**Dependencies**

```bash
tutorial-sdk add dependency PACKAGES...
tutorial-sdk add apt PACKAGES...
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
* `--output OUTPUT` (_optional_): Target path to save the generated Dockerfile. Defaults to standard configuration build path.

---

## `build`
Runs the build pipeline: generates the `Dockerfile`, writes configured
reproducibility and devcontainer artifacts, and compiles the final container
image using Docker.

```bash
tutorial-sdk build [--config CONFIG] \
                   [--image IMAGE] \
                   [--platform PLATFORM]
```

**Arguments**

* `--config CONFIG` (_optional_): Path to the YAML specification. Defaults to `./tutorial.yml`.
* `--image IMAGE` (_optional_): Sets the target image override for the resulting container image. Defaults to `[name]:latest` derived from configuration.
* `--platform PLATFORM` (_optional_): Sets the target platform for the resulting container image.

---

## `run`
Launches the built tutorial image locally. Automatically maps and binds the exposed container runtime ports.

```bash
tutorial-sdk run [--config CONFIG] \
                 [--image IMAGE] \
                 [--port PORT] \
                 [--shell]
```

**Arguments**

* `--config CONFIG` (_optional_): Path to the YAML specification. Defaults to `./tutorial.yml`.
* `--image IMAGE` (_optional_): Override target container image to run.
* `--port PORT` (_optional_): Host port to bind the container. Defaults to `8888`.
* `--shell` (_flag_, _optional_): Directs the launcher to boot into a standard shell (`/bin/sh`) inside the container instead of starting JupyterLab. Defaults to `false`.

---

## `validate`
Performs validation checks to ensure spec consistency, content file existence, import availability, and notebook execution hygiene.

```bash
tutorial-sdk validate [--config CONFIG] \
                      [--output OUTPUT] \
                      [--strict] \
                      [--container]
```

**Arguments**

* `--config CONFIG` (_optional_): Path to the YAML specification. Defaults to `./tutorial.yml`.
* `--output OUTPUT` (_optional_): Specifies the path to save the resulting JSON validation report. Defaults to `tutorial-validation.json`.
* `--strict` (_flag_, _optional_): When enabled, fails validation checks (exiting with non-zero code) on warnings. Defaults to `false`.
* `--container` (_flag_, _optional_): Enables live container validation against the configured or default image tag. This command does not build the image first; use `tutorial-sdk build` or `tutorial-sdk ci --mode test` when the image must be built before validation. Defaults to `false`.

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
tutorial-sdk ci [--mode MODE] \
                [--config CONFIG] \
                [--dockerfile DOCKERFILE] \
                [--image IMAGE] \
                [--push] \
                [--strict] \
                [--no-cache]
```

**Arguments**

* `--mode MODE` (_optional_): Specifies the CI run mode.
    - `validate` (_default_): Runs static validation only (generates `Dockerfile` and `tutorial-manifest.json`).
    - `build`: Builds the container image (generates `Dockerfile` and manifests internally).
    - `test`: Builds the container image, then runs live container validation inside the built image.
    - `publish`: Builds the container image and pushes to a registry (requires `--push`).
    - `all`: Combines build, live container validation, and optional push.
* `--config CONFIG` (_optional_): Path to `tutorial.yml`. Defaults to `./tutorial.yml`.
* `--dockerfile DOCKERFILE` (_optional_): Target generated Dockerfile path. Defaults to `./Dockerfile`.
* `--image IMAGE` (_optional_): Target tag name for the build image.
* `--push` (_flag_, _optional_): Whether to push the image to a container registry after building. Only applies to `publish` and `all` modes. Defaults to `false`.
* `--strict` (_flag_, _optional_): Whether to fail validation checks on warnings. Defaults to `false`.
* `--no-cache` (_flag_, _optional_): Disables Docker layer caching. Defaults to `false` (caching is enabled by default).

---

**CI Exit Code Protocol**

The `ci` subcommand returns clean exit codes so downstream GitHub Actions can route step failures accurately:

| Exit Code | Reason |
| :---: | :--- |
| `0` | **Success** — Workflow completed cleanly. |
| `1` | **Validation Failure** — One or more validation checks failed. |
| `2` | **Configuration Error** — Unable to parse or read `tutorial.yml`. |
| `3` | **Build Failure** — `docker build` failed to compile. |
| `4` | **Scaffold / Import Failure** — A scaffold template or import operation could not complete. |
| `5` | **Internal SDK Error** — System process exceptions occurred. |
