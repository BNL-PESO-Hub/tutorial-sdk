![logo](https://raw.githubusercontent.com/BNL-PESO-Hub/tutorial-sdk/main/docs/assets/images/tutorial-sdk-banner-modern.png)

# Tutorial SDK
[![PyPI version](https://img.shields.io/pypi/v/tutorial-sdk.svg?color=blue)](https://pypi.org/project/tutorial-sdk/)
[![Python versions](https://img.shields.io/pypi/pyversions/tutorial-sdk.svg)](https://pypi.org/project/tutorial-sdk/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-MkDocs-blue)](docs/index.md)

A **declarative, reproducible tutorial packaging system** and command-line tool that bridges the gap between technical content and stable containerized runtimes.

> 💡 **Focus on teaching. We'll handle the runtime.**

Tutorial environments tend to drift over time: Dockerfiles diverge from notebooks, dependencies go unpinned, and validation is left to CI pipelines assembled by hand. `tutorial-sdk` addresses this by driving the full lifecycle — resolving and locking dependencies, synthesizing deterministic Dockerfiles, validating notebooks locally and inside built containers, and publishing images through a standardized CI workflow — all from a single declarative config.

> [!IMPORTANT]
> _This project is currently under active development. While we strive for stability, expect API changes and evolving best practices as we approach our first official release._

---

## Key Features

* **Declarative Configuration (`tutorial.yml`)**: Declare the whole tutorial surface - metadata, contents, package environments, and validation rules - in a single, Pydantic-validated YAML file.
* **Deterministic Environment Synthesis**: Generates cache-friendly, reproducible Dockerfiles matching the exact dependency graph specified in your config.
* **Notebook-Aware Container Validation**: Runs comprehensive local checks (assets, dependencies, notebook error detection) as well as full container validation (verifying built containers start successfully and that JupyterLab runs flawlessly inside).
* **GitHub Actions Integration**: Built-in support for standard, thin CI workflows (`tutorial-sdk ci`) that validate, build, test, and publish images from pull requests and releases.
* **Extensible Scaffolding Templates**: Starter templates to quickly spin up beginner workshops, notebooks, course modules, or custom lab exercises.

---

## Documentation

For comprehensive guides, API references, and architecture details, refer to the following documentation files:

- **[Getting Started](docs/index.md)**: Features, quickstart commands, and standard project layouts.
- **[Configuration Schema](docs/config-schema.md)**: Reference manual for all options in `tutorial.yml`.
- **[Architecture & Design](docs/architecture.md)**: Deep dive into the modular layers, container validation model, and extension override blocks.
- **[CLI Reference](docs/cli.md)**: Reference details for CLI commands and usage examples.
- **[Public API](docs/api-public.md) & [Full SDK API](docs/api-package.md) References**: API surface specifications and implementation details for Orchestration (`TutorialProject`), Spec Models (`TutorialSpec`), Scaffolding, Generators, Builders, and Validators.

> [!NOTE]
> Online Documentation: https://bnl-peso-hub.github.io/tutorial-sdk/

---

## Acknowledgments

This project was inspired by and carries the core ideas of the [ExaWorks](http://exaworks.org) project and the subsequent implementation of the Tutorials management approach ([radical-cybertools/tutorials](https://github.com/radical-cybertools/tutorials)) developed by the [RADICAL Research Team](https://radical-cybertools.github.io).

---

## License

This project is licensed under the [Apache License, Version 2.0](LICENSE).
