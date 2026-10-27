"""Interface for ``python -m dls_ibek_yaml_editor``."""

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from . import __version__

__all__ = ["main"]

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def edit(
    target: Annotated[
        Path | None,
        typer.Argument(
            help="An IOC service directory (with a values.yaml), its config "
            "directory, or an ioc.yaml. A service directory without a "
            "config/ioc.yaml starts a new instance.",
        ),
    ] = None,
    image: Annotated[
        str | None,
        typer.Option(
            help="Generic IOC image to read entities from, instead of "
            "the one pinned in values.yaml"
        ),
    ] = None,
    generic_ioc: Annotated[
        Path | None,
        typer.Option(
            help="A local generic IOC checkout to read entities from, for "
            "unreleased versions"
        ),
    ] = None,
    defs: Annotated[
        Path | None,
        typer.Option(help="A folder of *.ibek.support.yaml to read entities from"),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Check the registry for a newer image under the same tag, "
            "instead of using the cached entity definitions",
        ),
    ] = False,
    list_entities: Annotated[
        bool,
        typer.Option(
            "--list-entities",
            help="Print the entities the instance can use, then exit",
        ),
    ] = False,
    validate: Annotated[
        bool,
        typer.Option(
            "--validate",
            help="Check the ioc.yaml with ibek, print any problems, then exit",
        ),
    ] = False,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version", "-v", callback=_version, is_eager=True, help="Print version"
        ),
    ] = None,
) -> None:
    """Create or edit an ibek ioc.yaml with an xeb-like GUI."""
    if list_entities or validate:
        from ruamel.yaml.error import YAMLError

        from .image import ImageError

        try:
            _report(target, image, generic_ioc, defs, refresh, list_entities, validate)
        except (OSError, ValueError, YAMLError, ImageError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        return

    try:
        from .gui.main_window import Sources, run
    except ImportError as exc:
        typer.echo(f"Cannot start the GUI: {exc}", err=True)
        raise typer.Exit(1) from exc
    raise typer.Exit(run(target, Sources(image, generic_ioc, defs, refresh)))


def _report(
    target: Path | None,
    image: str | None,
    generic_ioc: Path | None,
    defs: Path | None,
    refresh: bool,
    list_entities: bool,
    validate: bool,
) -> None:
    from .service import find_instance, load_catalogue, open_document
    from .validate import validate as check

    def log(text: str) -> None:
        typer.echo(text, err=True)

    instance = find_instance(target)
    catalogue = load_catalogue(
        instance, image, generic_ioc, defs, log=log, refresh=refresh
    )
    for warning in catalogue.warnings:
        log(f"WARNING: {warning}")
    if list_entities:
        typer.echo(f"# entities from {catalogue.source}")
        for module, types in catalogue.modules.items():
            typer.echo(f"{module}  ({catalogue.files[module]})")
            for entity_type in types:
                typer.echo(f"  {entity_type.name}")
                for param in entity_type.params.values():
                    default = "required" if param.required else repr(param.default)
                    choices = f" {param.choices}" if param.choices else ""
                    typer.echo(f"    {param.name}: {param.kind} = {default}{choices}")
    if validate:
        if instance.ioc_yaml is None or not instance.ioc_yaml.exists():
            log("no ioc.yaml to validate")
            raise typer.Exit(1)
        problems = check(open_document(instance), catalogue, instance.name)
        for problem in problems:
            typer.echo(str(problem))
        typer.echo(f"{instance.ioc_yaml}: {len(problems)} problem(s)", err=True)
        if problems:
            raise typer.Exit(1)


def main(args: Sequence[str] | None = None) -> None:
    app(args=list(args) if args is not None else None, prog_name="dls-ibek-yaml-editor")


if __name__ == "__main__":
    main()
