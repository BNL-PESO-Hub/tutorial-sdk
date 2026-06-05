# Available Modules

## Orchestration

::: tutorial_sdk.project.TutorialProject
::: tutorial_sdk.resolver.ResolvedTutorialProject
::: tutorial_sdk.resolver.TutorialResolver

## Specification Models

::: tutorial_sdk.spec.TutorialSpec
::: tutorial_sdk.spec.AuthorSpec
::: tutorial_sdk.spec.RuntimeSpec
::: tutorial_sdk.spec.DependencySpec
::: tutorial_sdk.spec.ContentSpec
::: tutorial_sdk.spec.DockerfileSections
::: tutorial_sdk.spec.BuildSpec
::: tutorial_sdk.spec.ValidationSpec
::: tutorial_sdk.spec.EntrypointSpec

## Configuration Loading

::: tutorial_sdk.config.load_config

## Scaffold & Import

::: tutorial_sdk.scaffold.templates.ProjectScaffolder
::: tutorial_sdk.scaffold.importer.ProjectImporter

## Generation

::: tutorial_sdk.generator.dockerfile.DockerfileGenerator
::: tutorial_sdk.generator.manifest.ManifestGenerator
::: tutorial_sdk.generator.devcontainer.DevcontainerGenerator

## Build & Runtime

::: tutorial_sdk.builder.docker.BuildResult
::: tutorial_sdk.builder.docker.DockerBuilder
::: tutorial_sdk.builder.local.LocalBuilder
::: tutorial_sdk.runtime.launcher.RunResult
::: tutorial_sdk.runtime.launcher.RuntimeLauncher

## Validation

::: tutorial_sdk.validator.report.ValidationCheck
::: tutorial_sdk.validator.report.ValidationReport
::: tutorial_sdk.validator.assets.AssetValidator
::: tutorial_sdk.validator.notebooks.NotebookValidator
::: tutorial_sdk.validator.dependencies.DependencyValidator
::: tutorial_sdk.validator.container.ContainerValidator

## Built-in Exceptions

::: tutorial_sdk.errors

## Extension Protocols

::: tutorial_sdk.plugins
    options:
      members:
        - ValidatorPlugin
        - TutorialGenerator
      show_root_heading: false
