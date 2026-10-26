[![CI](https://github.com/DiamondLightSource/dls-ibek-yaml-editor/actions/workflows/ci.yml/badge.svg)](https://github.com/DiamondLightSource/dls-ibek-yaml-editor/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/DiamondLightSource/dls-ibek-yaml-editor/branch/main/graph/badge.svg)](https://codecov.io/gh/DiamondLightSource/dls-ibek-yaml-editor)
[![PyPI](https://img.shields.io/pypi/v/dls-ibek-yaml-editor.svg)](https://pypi.org/project/dls-ibek-yaml-editor)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

# dls-ibek-yaml-editor

An xeb-like GUI for creating and editing ibek `ioc.yaml` files.

You point it at an IOC service directory, for example
`deb-services/services/fe99i-mo-ioc-01`. The tool reads the generic IOC image
pinned in that directory's `values.yaml`. It then fetches that image's
`/epics/ibek-defs` straight from the container registry. You don't need docker,
podman or a local checkout. The download is cached, so it happens once per
image tag. Those definitions include the modules inherited from the base image
(asyn, iocStats, autosave, …). So the editor offers exactly the entities that
version of the generic IOC can instantiate.

What            | Where
:---:           | :---:
Source          | <https://github.com/DiamondLightSource/dls-ibek-yaml-editor>
PyPI            | `pip install dls-ibek-yaml-editor`
Releases        | <https://github.com/DiamondLightSource/dls-ibek-yaml-editor/releases>

## Usage

```
uvx dls-ibek-yaml-editor services/fe99i-mo-ioc-01            # edit, or create a new instance
uvx dls-ibek-yaml-editor services/fe99i-mo-ioc-01 --validate # check it with ibek
uvx dls-ibek-yaml-editor services/fe99i-mo-ioc-01 --list-entities
```

The argument can be any of these:

- a service directory
- its `config` directory
- an `ioc.yaml`

If a service directory has no `config/ioc.yaml` yet, the tool starts a new
instance and writes the file on the first save. The new file's `$schema`
header points at the schema published for the pinned image tag, so VS Code
can validate the file outside the container.

These options change where the entities come from:

Option                | Entities come from
:---                  | :---
(default)             | the image in `values.yaml`
`--image REF`         | another image
`--generic-ioc PATH`  | a local generic IOC checkout, for an unreleased version: its Dockerfile's support modules, plus the base image's from the registry. A warning is shown if the checkout's tag differs from `values.yaml`.
`--defs DIR`          | a folder of `*.ibek.support.yaml`

The instance's own `config/*.ibek.support.yaml` are always added on top.

## The window

The layout follows xeb:

- **Left:** the entity types used in the file. Pick one to edit its table.
  Rows are entities and columns are parameters.
- **Components menu:** adds a type.
- **Undo stack:** in a dock on the left.

Inside the table:

- Defaults are shown in grey italics and are not written to the file.
- Required cells that are empty are red. Bold column headers are required
  parameters.
- Enums and booleans are edited with drop-downs. Object references offer the
  ids defined in the file.

The Edit menu keeps xeb's commands and shortcuts:

- Insert Row (Ctrl+I) and Insert Row Under (Ctrl+U)
- Cut, copy and paste (tab-separated, so spreadsheets work)
- Clear (Ctrl+D)
- Fill Cells (Ctrl+L)
- Fill Cells and Increment (Ctrl+R), which turns `MOT.APTR03X` into
  `MOT.APTR04X`, `MOT.APTR05X`, …

The order of `entities` is the IOC's startup order. As in xeb, each type's
entities are kept together, and the order of the type list is the order of the
file. Drag a type in the list, or use Components > Move Table Up/Down, to move
a whole table. Drag a row header to reorder entities within a table. If a file
interleaves types, it is grouped by type when it is opened. That step can be
undone, and nothing is written until you save. ibek needs an entity to come
before anything that refers to its id, and validation reports a reference that
comes too early.

**Tools > Validate** (F5) checks the file with ibek's own model. It runs again
on every save, and the problems are listed and highlighted in the tables.

Saving changes only the lines you edited. Comments, quoting and Jinja are kept.

## Registry access

Images are pulled anonymously. If a registry needs credentials, they are read
from `~/.docker/config.json` (`docker login`) or from `CI_REGISTRY_*`. Fetched
definitions are cached in `~/.cache/dls-ibek-yaml-editor`, or wherever
`DLS_IBEK_YAML_EDITOR_CACHE` points. A cached tag is used without contacting
the registry, so later runs start at once and work offline. Pass `--refresh`
to check the registry for an image rebuilt under the same tag (`latest` is
always checked).

With no target, `--image`, `--generic-ioc` or `--defs` open an untitled
instance using those entities; Save asks where to write it.
