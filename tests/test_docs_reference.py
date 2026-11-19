"""The reference pages stay in step with the CLI parser and the spec models.

``docs/cli.md`` must list every subcommand option in the usage block and
in an option bullet, with its choices and default (and nothing the
parser does not accept), and ``docs/config-schema.md`` must list every
field of the specification models with its default.
"""

import argparse
import re
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from tutorial_sdk import spec
from tutorial_sdk.cli import _build_parser

DOCS = Path(__file__).resolve().parents[1] / "docs"

pytestmark = pytest.mark.skipif(
    not DOCS.is_dir(), reason="documentation sources not available"
)


def _subcommands(parser, prefix=()):
    """Yield ``(name parts, parser)`` for every leaf subcommand."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _subcommands(sub, (*prefix, name))
            return
    yield prefix, parser


def _section(text, title):
    """Return the body of the ``## `title``` section."""
    match = re.search(
        rf"^## `{re.escape(title)}`\n(.*?)(?=^## |\Z)", text, re.M | re.S
    )
    assert match, f"docs/cli.md has no section for `{title}`"
    return match.group(1)


def _option_bullets(body):
    """Map each ``* `--option ...``` bullet of a section to its text.

    A bullet runs to the next blank line or the next ``*`` line, so
    wrapped text and the indented list of ``ci`` modes belong to it.
    """
    bullets = {}
    current = None
    for line in body.splitlines():
        match = re.match(r"\* `(--[\w-]+)", line)
        if match:
            current = match.group(1)
            assert current not in bullets, f"{current}: duplicate bullet"
            bullets[current] = line
        elif current and line.strip() and not line.startswith("*"):
            bullets[current] += "\n" + line
        else:
            current = None
    return bullets


def _usage_options(usage, parts):
    """Return the options of the usage lines for one leaf subcommand."""
    options = set()
    found = False
    for command in usage.replace("\\\n", " ").splitlines():
        words = command.split()
        if words[: len(parts) + 1] == ["tutorial-sdk", *parts]:
            found = True
            options.update(re.findall(r"(?<![\w-])--[a-z][\w-]*", command))
    assert found, f"docs/cli.md has no usage line for `{' '.join(parts)}`"
    return options


def _documented_cli_default(text):
    """Return the default a bullet states (``./`` stripped), if any."""
    match = re.search(r"Defaults to `([^`]*)`", text) or re.search(
        r"`([^`]*)` \(_default_\)", text
    )
    return match.group(1).removeprefix("./") if match else None


CLI_CASES = [
    (parts, sub)
    for parts, sub in _subcommands(_build_parser())
    if parts  # the bare program has no options of its own
]


@pytest.mark.parametrize(
    ("parts", "sub"), CLI_CASES, ids=[" ".join(p) for p, _ in CLI_CASES]
)
def test_cli_page_documents_every_option(parts, sub):
    name = " ".join(parts)
    body = _section((DOCS / "cli.md").read_text(), parts[0])
    synopsis = body.split("**Arguments**")[0]
    usage = "\n".join(re.findall(r"```bash\n(.*?)```", synopsis, re.S))
    assert usage, f"docs/cli.md has no usage block for `{parts[0]}`"
    bullets = _option_bullets(body)
    accepted = {}
    for action in sub._actions:
        options = [o for o in action.option_strings if o.startswith("--")]
        if not options or options == ["--help"]:
            continue
        accepted.update(dict.fromkeys(options, action))
    in_usage = _usage_options(usage, parts)
    assert in_usage == set(accepted), f"{name}: usage line options differ"
    assert set(bullets) == set(accepted), f"{name}: option bullets differ"
    for option, action in accepted.items():
        if action.choices:
            listed = "{" + ",".join(action.choices) + "}"
            assert listed in bullets[option], f"{name}: {listed} undocumented"
        default = action.default
        documented = _documented_cli_default(bullets[option])
        if default is None or default == "" or default is False:
            # Nothing to state (the prose may describe a fallback);
            # flags that state a default must say `false`.
            if default is False and documented is not None:
                assert documented == "false", f"{name}: {option} default"
            continue
        assert documented == str(default), f"{name}: {option} default"


SCHEMA_SECTIONS = {
    "general": spec.TutorialSpec,
    "runtime": spec.RuntimeSpec,
    "dependencies": spec.DependencySpec,
    "content": spec.ContentSpec,
    "build": spec.BuildSpec,
    "validation": spec.ValidationSpec,
    "entrypoint": spec.EntrypointSpec,
}


def _schema_rows(title):
    text = (DOCS / "config-schema.md").read_text()
    match = re.search(
        rf"^### \d+\. `{title}`\n(.*?)(?=^---|^### |\Z)", text, re.M | re.S
    )
    assert match, f"docs/config-schema.md has no section for `{title}`"
    rows = {}
    for line in match.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        field = re.fullmatch(r"`(\w+)`", cells[0]) if cells else None
        if field and len(cells) >= 3:
            rows[field.group(1)] = cells[2]
    return rows


def _documented_default(cell):
    """Normalise a default cell: ``"x"``, ``True``, ``[]``, ``None``"""
    if "required" in cell:
        return PydanticUndefined
    match = re.match(r"`([^`]*)`", cell)
    return match.group(1) if match else cell


def _model_default(info):
    """Render a field default as the page writes it (``None`` if nested)."""
    if info.is_required():
        return PydanticUndefined
    if info.default_factory is not None:
        value = info.default_factory()
        return None if isinstance(value, BaseModel) else repr(value)
    value = info.default
    return f'"{value}"' if isinstance(value, str) else str(value)


@pytest.mark.parametrize("title", sorted(SCHEMA_SECTIONS))
def test_schema_page_matches_models(title):
    model = SCHEMA_SECTIONS[title]
    rows = _schema_rows(title)
    # Top-level sections (runtime, build, ...) have tables of their own.
    fields = {
        name: info
        for name, info in model.model_fields.items()
        if name not in SCHEMA_SECTIONS
    }
    assert set(rows) == set(fields), f"`{title}` fields differ"
    for name, info in fields.items():
        expected = _model_default(info)
        if expected is None:  # nested model: no scalar default
            continue
        assert _documented_default(rows[name]) == expected, (
            f"`{title}.{name}` default"
        )
