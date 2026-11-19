# Full SDK API Reference

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

## Container Backend Selection

::: tutorial_sdk.backend.get_backend
::: tutorial_sdk.backend.preference_source
::: tutorial_sdk.backend.BACKEND_CHOICES
::: tutorial_sdk.backend.BackendName

## Container Backend Protocol

::: tutorial_sdk.backend.base.ContainerBackend
::: tutorial_sdk.backend.base.LogCallback
::: tutorial_sdk.backend.base.ImageBuildResult
::: tutorial_sdk.backend.base.ImagePushResult
::: tutorial_sdk.backend.base.ContainerHandle
::: tutorial_sdk.backend.base.ExecResult
::: tutorial_sdk.backend.base.RunExit
::: tutorial_sdk.backend.base.BackendInfo
::: tutorial_sdk.backend.base.ManagedContainer
::: tutorial_sdk.backend.base.MANAGED_LABEL
::: tutorial_sdk.backend.base.MANAGED_LABEL_VALUE
::: tutorial_sdk.backend.base.STOP_GRACE
::: tutorial_sdk.backend.base.STOP_HINT
::: tutorial_sdk.backend.base.observe_running
::: tutorial_sdk.backend.base.trap_signals

## Container Backend Implementations

::: tutorial_sdk.backend.docker_cli.DockerCliBackend
::: tutorial_sdk.backend.docker_cli.foreground_command
::: tutorial_sdk.backend.docker_cli.tracked_command
::: tutorial_sdk.backend.docker_sdk.DockerSdkBackend
::: tutorial_sdk.backend.docker_sdk.requires_buildkit

## Container Backend Errors

::: tutorial_sdk.backend.errors

## Diagnostics

::: tutorial_sdk.doctor.diagnose
::: tutorial_sdk.doctor.DoctorReport
::: tutorial_sdk.doctor.BackendStatus
::: tutorial_sdk.doctor.Selection

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
