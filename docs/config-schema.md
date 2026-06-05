# Configuration Schema Reference

The single source of truth for a tutorial package is the `tutorial.yml` file. Its schema is parsed and strictly validated by Pydantic models defined in `src/tutorial_sdk/spec.py`.

---

## Schema Overview

A complete, fully detailed `tutorial.yml` specification layout is structured as follows:

```yaml
name: advanced-data-science
version: 0.2.0
title: Advanced Data Science Workshop
description: Hands-on scientific data engineering and visualization.
license: Apache-2.0

authors:
  - name: Misha Ti
    email: name@domain

runtime:
  language: python
  python: "3.11"
  kernel: python3
  jupyterlab: true
  expose_port: 8888

dependencies:
  apt:
    - git
    - build-essential
  pip:
    - numpy
    - pandas
    - matplotlib
  conda:
    - scipy
  local:
    - .

content:
  notebooks:
    - notebooks/01-introduction.ipynb
    - notebooks/02-data-cleaning.ipynb
  scripts:
    - src/helpers.py
  data:
    - data/sensor-readings.csv
  docs:
    - README.md
  exercises:
    - notebooks/exercises/01-exercise.ipynb
  solutions:
    - notebooks/solutions/01-solution.ipynb

build:
  dockerfile: Dockerfile
  image: advanced-data-science:latest
  base_image: python:3.11-slim
  copy_repo: true
  preexecute_notebooks: false
  export_devcontainer: true
  export_manifest: true
  cache: true
  custom_sections:
    before_dependencies: docker/before-deps.Dockerfile
    after_dependencies: docker/after-deps.Dockerfile
    before_entrypoint: docker/before-entry.Dockerfile

validation:
  execute_notebooks: false
  check_imports: true
  check_links: true
  require_clean_execution: true

entrypoint:
  kind: jupyterlab
  default_notebook: notebooks/01-introduction.ipynb
  # command: ["python", "app.py"]
```

---

## Section Reference

### 1. `general`
Top-level configuration keys describing general package details.

| Field | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `name` | `str` | *required* | Machine name of the tutorial (used for file structures and image tags). *Must not be empty.* |
| `version` | `str` | `"0.1.0"` | Current version of the tutorial package. |
| `title` | `str` | `None` | Display title exposed in generated metadata. *Fallbacks to `name` if omitted.* |
| `description` | `str` | `""` | A brief explanation summarizing the tutorial. |
| `license` | `str` | `None` | Open-source or custom license identifier (e.g., `"Apache-2.0"`). |
| `authors` | `list[AuthorSpec]` | `[]` | List of author objects with `name` (required) and `email` (optional). |

---

### 2. `runtime`
Defines target execution platforms and options for the container environment.

| Field | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `language` | `str` | `"python"` | Main programming language used in the tutorial. *Note: Currently always "python".* |
| `python` | `str` | `"3.11"` | Python version configured in package environments. *Note: Currently informational; the python version is defined by the base image.* |
| `kernel` | `str` | `"python3"` | Default registered Jupyter kernel name. *Note: Currently informational; defaults to "python3".* |
| `jupyterlab` | `bool` | `True` | Exposes the configured runtime port in the generated Dockerfile when true. Declare `jupyterlab` in `dependencies.pip`, or provide it in the base image, when using a JupyterLab entrypoint or container validation. |
| `expose_port` | `int` | `8888` | Main network port exposed by the container runtime. |

---

### 3. `dependencies`
System and package library dependencies that are built into the image.

| Field | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `apt` | `list[str]` | `[]` | Debian package requirements (e.g. `git`, `curl`, `g++`) installed via **apt**. |
| `pip` | `list[str]` | `[]` | Python libraries (e.g., `numpy`, `pandas`) installed via **pip**. |
| `conda` | `list[str]` | `[]` | Packages intended for installation via **conda**. The current Dockerfile generator records these as comments unless the selected base image and custom sections provide conda support. |
| `local` | `list[str]` | `[]` | Local paths to install via **pip** in editable/development mode (e.g., `.`). |

---

### 4. `content`
Explicit layout of content and asset paths copied into the workspace.

| Field | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `notebooks` | `list[str]` | `[]` | Declared interactive Jupyter notebook paths. |
| `scripts` | `list[str]` | `[]` | Helper python scripts or executable tools. |
| `data` | `list[str]` | `[]` | Accompanying CSVs, sqlite databases, or dataset files. |
| `docs` | `list[str]` | `[]` | Markdown documentation or helper readme files. |
| `exercises` | `list[str]` | `[]` | Assignment/exercise notebook files. |
| `solutions` | `list[str]` | `[]` | Reference notebook solutions. |

---

### 5. `build`
Configures build generation behaviors, targets, and caching.

| Field | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `dockerfile` | `str` | `"Dockerfile"` | Path where the generated Dockerfile should be written. |
| `image` | `str` | `None` | Target container image tag name to compile or publish. |
| `base_image` | `str` | `"python:3.11-slim"` | The target **Docker base image** to build on top of. |
| `copy_repo` | `bool` | `True` | Copies the entire source repository to `/workspace` when true. When false, only declared content paths are copied. |
| `preexecute_notebooks`| `bool` | `False` | Performs build-time notebook pre-execution. *Note: Future feature (not currently in use).* |
| `export_devcontainer` | `bool` | `False` | Generates a corresponding `.devcontainer/devcontainer.json` configuration. |
| `export_manifest` | `bool` | `True` | Automatically outputs `tutorial-manifest.json` on build execution. |
| `cache` | `bool` | `True` | Directs the builder to utilize standard Docker layer caching. |
| `custom_sections` | `DockerfileSections` | `n/a (empty)` | Relates custom Dockerfile snippet files: `before_dependencies`, `after_dependencies`, `before_entrypoint`. |

---

### 6. `validation`
Enables individual testing metrics.

| Field | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `execute_notebooks` | `bool` | `False` | Enables notebook compilation executions. *Note: Future feature (not currently in use).* |
| `check_imports` | `bool` | `True` | Checks whether import-like pip dependencies are importable in the current Python environment. Missing imports are reported as warnings. |
| `check_links` | `bool` | `True` | Reserved for link checks. *Note: Future feature (not currently in use).* |
| `require_clean_execution` | `bool` | `True` | Rejects notebooks that contain pre-existing cell execution traces or stack errors. |

---

### 7. `entrypoint`
Container startup settings.

| Field | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `kind` | `str` | `"jupyterlab"`| Startup target style: `"jupyterlab"`, `"shell"`, or `"command"`. |
| `default_notebook` | `str` | `None` | Path to default opening notebook in JupyterLab. |
| `command` | `list[str]`| `None` | The literal command list to execute in `"command"` entrypoint style. |
