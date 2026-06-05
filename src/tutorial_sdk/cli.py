"""Command-line interface for tutorial-sdk."""

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG
from .errors import BuildError, ConfigError, ScaffoldError, TutorialSdkError
from .project import TutorialProject
from .builder.local import LocalBuilder, DEFAULT_MANIFEST_NAME
from .scaffold import ProjectScaffolder

DEFAULT_VALIDATION_REPORT_NAME: str = "tutorial-validation.json"


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except BuildError as exc:
        print(f"Build error: {exc}", file=sys.stderr)
        return 3
    except ScaffoldError as exc:
        print(f"Import error: {exc}", file=sys.stderr)
        return 4
    except TutorialSdkError as exc:
        print(f"tutorial-sdk error: {exc}", file=sys.stderr)
        return 5


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser.

    Returns:
        Fully configured ``ArgumentParser`` with all
        subcommands registered.
    """

    parser = argparse.ArgumentParser(prog="tutorial-sdk")
    subparsers = parser.add_subparsers(required=True)

    init = subparsers.add_parser("init")
    init.add_argument("path", nargs="?", default=None)
    init.add_argument(
        "--from",
        dest="from_dir",
        default=None,
        metavar="DIR",
        help="Import an existing project directory.",
    )
    init.add_argument(
        "--url",
        default=None,
        metavar="URL",
        help="Clone and import a remote git repository.",
    )
    init.add_argument(
        "--github",
        default=None,
        metavar="ORG/REPO",
        help="Import a GitHub repository (ORG/REPO).",
    )
    init.add_argument(
        "--remove-clone",
        action="store_true",
        default=False,
        help=(
            "Delete the cloned repository after "
            "importing (only applies to --url/--github)."
        ),
    )
    init.set_defaults(func=_init)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--config", default=DEFAULT_CONFIG)
    inspect.set_defaults(func=_inspect)

    add = subparsers.add_parser("add")
    add_sub = add.add_subparsers(required=True)
    for kind in (
        "notebook",
        "script",
        "data",
        "doc",
        "exercise",
        "solution",
    ):
        sub = add_sub.add_parser(kind)
        sub.add_argument("paths", nargs="+")
        sub.add_argument("--config", default=DEFAULT_CONFIG)
        sub.set_defaults(func=_add, kind=kind)
    add_dep = add_sub.add_parser("dependency")
    add_dep.add_argument("packages", nargs="+")
    add_dep.add_argument("--config", default=DEFAULT_CONFIG)
    add_dep.set_defaults(func=_add_dependency)
    add_apt = add_sub.add_parser("apt")
    add_apt.add_argument("packages", nargs="+")
    add_apt.add_argument("--config", default=DEFAULT_CONFIG)
    add_apt.set_defaults(func=_add_apt)

    generate = subparsers.add_parser("generate")
    generate_sub = generate.add_subparsers(required=True)
    dockerfile = generate_sub.add_parser("dockerfile")
    dockerfile.add_argument("--config", default=DEFAULT_CONFIG)
    dockerfile.add_argument("--output", default=None)
    dockerfile.set_defaults(func=_generate_dockerfile)

    build = subparsers.add_parser("build")
    build.add_argument("--config", default=DEFAULT_CONFIG)
    build.add_argument("--image", default=None)
    build.add_argument("--platform", default=None)
    build.set_defaults(func=_build)

    run = subparsers.add_parser("run")
    run.add_argument("--config", default=DEFAULT_CONFIG)
    run.add_argument("--image", default=None)
    run.add_argument("--port", type=int, default=8888)
    run.add_argument("--shell", action="store_true")
    run.set_defaults(func=_run)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--config", default=DEFAULT_CONFIG)
    validate.add_argument("--strict", action="store_true")
    validate.add_argument("--container", action="store_true")
    validate.add_argument(
        "--output",
        default=DEFAULT_VALIDATION_REPORT_NAME,
    )
    validate.set_defaults(func=_validate)

    scaffold = subparsers.add_parser("scaffold")
    scaffold.add_argument("template")
    scaffold.add_argument("--name", required=True)
    scaffold.add_argument("--path", default=None)
    scaffold.set_defaults(func=_scaffold)

    ci = subparsers.add_parser("ci")
    ci.add_argument("--mode", default="validate")
    ci.add_argument("--config", default=DEFAULT_CONFIG)
    ci.add_argument("--dockerfile", default="Dockerfile")
    ci.add_argument("--image", default="")
    ci.add_argument("--push", action="store_true")
    ci.add_argument("--strict", action="store_true")
    ci.add_argument("--no-cache", action="store_true")
    ci.set_defaults(func=_ci)
    return parser


def _load_project(config: str) -> TutorialProject:
    """Load a tutorial project from a config path.

    Args:
        config: Path to the tutorial YAML file.

    Returns:
        Loaded ``TutorialProject`` instance.
    """

    return TutorialProject.load(config)


def _init(args: argparse.Namespace) -> int:
    """Handle the ``init`` subcommand.

    Creates a new tutorial project from a template,
    local directory, URL, or GitHub shorthand.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (``0`` on success).
    """

    target = args.path
    if args.from_dir:
        TutorialProject.init_from(args.from_dir, target)
    elif args.url:
        TutorialProject.init_from_url(
            args.url,
            target,
            remove_clone=args.remove_clone,
        )
    elif args.github:
        TutorialProject.init_from_github(
            args.github,
            target,
            remove_clone=args.remove_clone,
        )
    else:
        TutorialProject.init(target)
    return 0


def _inspect(args: argparse.Namespace) -> int:
    """Handle the ``inspect`` subcommand.

    Prints resolved tutorial metadata as JSON.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (``0``).
    """

    print(_load_project(args.config).inspect(), end="")
    return 0


_CONTENT_FIELD_MAP = {
    "notebook": "notebooks",
    "script": "scripts",
    "data": "data",
    "doc": "docs",
    "exercise": "exercises",
    "solution": "solutions",
}


def _add(args: argparse.Namespace) -> int:
    """Add content paths to the tutorial spec."""

    project = _load_project(args.config)
    field = _CONTENT_FIELD_MAP[args.kind]
    current = list(getattr(project.spec.content, field))
    for path in args.paths:
        if path not in current:
            current.append(path)
    project.spec = project.spec.model_copy(
        update={
            "content": project.spec.content.model_copy(
                update={field: current},
            ),
        },
    )
    project.spec.write(project.config_path)
    return 0


def _add_dependency(args: argparse.Namespace) -> int:
    """Add pip dependencies to the tutorial spec."""

    project = _load_project(args.config)
    current = list(project.spec.dependencies.pip)
    for pkg in args.packages:
        if pkg not in current:
            current.append(pkg)
    project.spec = project.spec.model_copy(
        update={
            "dependencies": project.spec.dependencies.model_copy(
                update={"pip": current},
            ),
        },
    )
    project.spec.write(project.config_path)
    return 0


def _add_apt(args: argparse.Namespace) -> int:
    """Add apt packages to the tutorial spec."""

    project = _load_project(args.config)
    current = list(project.spec.dependencies.apt)
    for pkg in args.packages:
        if pkg not in current:
            current.append(pkg)
    project.spec = project.spec.model_copy(
        update={
            "dependencies": project.spec.dependencies.model_copy(
                update={"apt": current},
            ),
        },
    )
    project.spec.write(project.config_path)
    return 0


def _generate_dockerfile(args: argparse.Namespace) -> int:
    """Handle the ``generate dockerfile`` subcommand.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (``0``).
    """

    project = _load_project(args.config)
    LocalBuilder(project.spec, project.root).write_dockerfile(args.output)
    return 0


def _build(args: argparse.Namespace) -> int:
    """Handle the ``build`` subcommand.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (``0``).
    """

    _load_project(args.config).build(
        image=args.image,
        platform=args.platform,
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    """Handle the ``run`` subcommand.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Container exit code.
    """

    result = _load_project(args.config).run(
        image=args.image,
        port=args.port,
        shell=args.shell,
    )
    return result.returncode


def _validate(args: argparse.Namespace) -> int:
    """Handle the ``validate`` subcommand.

    Runs validation checks and writes a JSON report.

    Args:
        args: Parsed CLI arguments.

    Returns:
        ``0`` if all checks passed, ``1`` otherwise.
    """

    project = _load_project(args.config)
    report = project.validate(
        strict=args.strict,
        container=args.container,
    )
    report.write(Path(project.root) / args.output)

    print(report.render_json(), end="")
    return 0 if report.passed else 1


def _scaffold(args: argparse.Namespace) -> int:
    """Handle the ``scaffold`` subcommand.

    Creates a new tutorial project from a named
    template.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Exit code (``0``).
    """

    path = args.path or args.name
    ProjectScaffolder().scaffold(args.template, path, args.name)
    return 0


def _ci(args: argparse.Namespace) -> int:
    """Handle the ``ci`` subcommand.

    Orchestrates a CI workflow that may include
    validation, building, testing, and publishing.

    Args:
        args: Parsed CLI arguments.

    Returns:
        ``0`` on success, non-zero on failure.
    """

    import json
    import time

    project = _load_project(args.config)

    # Override build settings without mutating the original spec.
    build_overrides: dict[str, object] = {
        "dockerfile": args.dockerfile,
    }
    if args.image:
        build_overrides["image"] = args.image
    project.spec = project.spec.model_copy(
        update={
            "build": project.spec.build.model_copy(
                update=build_overrides,
            ),
        },
    )

    mode = args.mode
    strict = args.strict
    push = args.push

    logs: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        logs.append(msg)

    log("Starting tutorial-sdk CI workflow...")
    log(f"Mode: {mode}")
    log(f"Config: {args.config}")
    log(f"Dockerfile: {args.dockerfile}")

    def _write_logs() -> None:
        log_path = Path(project.root) / "tutorial-sdk.log"
        with log_path.open("w") as stream:
            stream.write("\n".join(logs) + "\n")

    if mode == "validate":
        log("Running local validation...")
        report = project.validate(
            strict=strict,
            container=False,
        )
        val_path = Path(project.root) / DEFAULT_VALIDATION_REPORT_NAME
        report.write(val_path)
        _emit_github_output(
            "validation_report",
            DEFAULT_VALIDATION_REPORT_NAME,
        )
        _emit_github_output(
            "passed",
            str(report.passed).lower(),
        )
        builder = LocalBuilder(project.spec, project.root)
        builder.write_dockerfile(args.dockerfile)
        builder.write_manifest()
        if not report.passed:
            log("Validation failed.")
            _write_logs()
            return 1
        log("Validation passed.")

    elif mode in {"build", "test", "publish", "all"}:
        log("Building container image...")
        try:
            start_build = time.time()
            build_result = project.build(
                image=args.image or None,
                no_cache=args.no_cache,
            )
            duration = time.time() - start_build
            log(f"Container built in {duration:.2f}s: {build_result.image}")

            pushed = False
            digest = None
            if mode in {"publish", "all"} and push:
                pushed, digest = _docker_push(
                    build_result.image,
                    log,
                )

            build_report = {
                "image": build_result.image,
                "dockerfile": str(
                    build_result.dockerfile.relative_to(
                        project.root,
                    )
                ),
                "pushed": pushed,
                "digest": digest,
                "duration_seconds": round(duration, 2),
                "status": build_result.status,
            }
            build_path = Path(project.root) / "tutorial-build.json"
            with build_path.open("w") as f:
                json.dump(build_report, f, indent=2)
        except BuildError as exc:
            log(f"Build failed: {exc}")
            build_report = {
                "image": (args.image or project.resolve().image),
                "dockerfile": args.dockerfile,
                "pushed": False,
                "digest": None,
                "duration_seconds": 0.0,
                "status": "failure",
            }
            build_path = Path(project.root) / "tutorial-build.json"
            with build_path.open("w") as f:
                json.dump(build_report, f, indent=2)
            _write_logs()
            return 3

        if mode in {"test", "all"}:
            log("Running container validation...")
            report = project.validate(
                strict=strict,
                container=True,
                image=build_result.image,
            )
            val_path = Path(project.root) / DEFAULT_VALIDATION_REPORT_NAME
            report.write(val_path)
            _emit_github_output(
                "validation_report",
                DEFAULT_VALIDATION_REPORT_NAME,
            )
            _emit_github_output(
                "passed",
                str(report.passed).lower(),
            )
            if not report.passed:
                log("Container validation failed.")
                _write_logs()
                return 1
            log("Container validation passed.")

    image_tag = args.image or project.resolve().image
    _emit_github_output("image", image_tag)
    _emit_github_output("dockerfile", args.dockerfile)
    _emit_github_output("manifest", DEFAULT_MANIFEST_NAME)

    log("CI workflow completed successfully.")
    _write_logs()
    return 0


def _docker_push(
    image: str,
    log: object,
) -> tuple[bool, str | None]:
    """Push a Docker image and return (pushed, digest)."""

    import subprocess

    log(f"Pushing image {image}...")
    result = subprocess.run(
        ["docker", "push", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        log(f"Push failed: {result.stderr.strip()}")
        return False, None
    log(f"Image {image} pushed successfully.")

    # Attempt to extract digest from inspect.
    inspect = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{index .RepoDigests 0}}",
            image,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    digest = None
    if inspect.returncode == 0:
        raw = inspect.stdout.strip()
        if "@" in raw:
            digest = raw.split("@", 1)[1]
    return True, digest


def _emit_github_output(
    name: str,
    value: str,
) -> None:
    """Write a key=value pair to GITHUB_OUTPUT."""

    import os

    env_path = os.environ.get("GITHUB_OUTPUT")
    if env_path:
        with Path(env_path).open("a") as stream:
            stream.write(f"{name}={value}\n")


if __name__ == "__main__":
    raise SystemExit(main())
