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
podman or a local checkout. The definitions are cached for reuse on later runs.
Those definitions include the modules inherited from the base image
(asyn, iocStats, autosave, …). So the editor offers exactly the entities that
version of the generic IOC can instantiate.

What            | Where
:---:           | :---:
Source          | <https://github.com/DiamondLightSource/dls-ibek-yaml-editor>
PyPI            | `pip install dls-ibek-yaml-editor`
Releases        | <https://github.com/DiamondLightSource/dls-ibek-yaml-editor/releases>
Code structure  | [docs/architecture.md](docs/architecture.md)
Development and testing | [docs/development.md](docs/development.md)

## Usage

Requires Python 3.13 or later and a desktop environment that can run Qt.

```sh
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
header points at the schema published for the pinned image tag when a schema
URL can be derived from it, so VS Code can validate the file outside the
container. Services using `compose.yml` or `compose.yaml` are also recognised.

`--validate` and `--list-entities` run without opening a window or requiring a
display. Validation exits with status 0 on success and 1 if there are problems
or the IOC file is missing.

These options change where the entities come from:

Option                | Entities come from
:---                  | :---
(default)             | the image in `values.yaml`
`--image REF`         | another image
`--generic-ioc PATH`  | a local generic IOC checkout, for an unreleased version: its Dockerfile's support modules, plus the base image's from the registry. A warning is shown if the checkout's tag differs from `values.yaml`.
`--defs DIR`          | a folder of `*.ibek.support.yaml`

If combined, `--defs` takes priority over `--generic-ioc`, which takes priority
over the image. Support files beside the target `ioc.yaml` (normally
`config/*.ibek.support.yaml`) are always added on top. A local definition
replaces the whole module with the same name. Invalid support files and
missing definition directories are reported as errors.

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
- Enums and booleans are edited with editable drop-downs, so Jinja values
  can be retained. Object references offer the ids of active entities in the
  file.
- The first column, **X**, comments a row out, as in xeb. The entity is saved
  as a commented-out block (`  # - type: …`), as VS Code's Ctrl+/ would write
  it, and blocks like that in an existing file open as X rows. ibek ignores a
  commented entity, so validation skips it and its ids can't be referenced.
  Clear the cell (Ctrl+D) to uncomment it.

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
comes too early. This check applies to object references; plain string links,
such as some `port` parameters, are not checked.

**Tools > Validate** (F5) checks the file with ibek's own model. It runs again
on every save, and the problems are listed and highlighted in the tables.
Editing clears the previous results; press F5 to check again. If validation
finds problems when saving, you can cancel or choose **Save anyway**.

Dictionary and list parameters accept YAML or JSON in the cell. Clipboard text
uses quoted tab-separated values for spreadsheets; backslashes stay literal.
Clearing a cell removes its key. Existing null and empty-string values are
preserved until edited. Types missing from the definitions have read-only cells.

Saving retains comments, quoting and Jinja text, but serialises the whole
document, so spacing and indentation may change. Commented entities stay
commented out. Invalid entity collections are rejected when opening a file.
The file is replaced only after a complete temporary copy has been written.
Existing symlinks are followed and file permissions retained; new files get
the usual permissions for your umask (0664 with umask 002). A file owned by
someone else, or in a directory you can't create files in, is rewritten in
place instead, so its owner doesn't change.

## Registry access

Public images can be fetched anonymously. For private registries, credentials
are read from inline `auth` entries in `~/.docker/config.json` (or
`$DOCKER_CONFIG/config.json`), then from matching `CI_REGISTRY`,
`CI_REGISTRY_USER` and `CI_REGISTRY_PASSWORD`. Docker credential helpers are
not supported.

Fetched definitions are cached in `~/.cache/dls-ibek-yaml-editor`, or wherever
`DLS_IBEK_YAML_EDITOR_CACHE` points. A cached tag is used without contacting
the registry, so later runs start at once and work offline. Pass `--refresh`
to check the registry for an image rebuilt under the same tag (`latest` is
always checked). If a refresh fails, an existing valid cache is used with a
message explaining why.

The first fetch scans all image layers to include inherited definitions and
respect replacements and deletions. Refreshes keep older cache snapshots so
open documents can still use them. To reclaim space, close the editor and
remove its cache directory; the next run fetches definitions again. Caches
from older editor versions are rebuilt once.

With no target, `--image`, `--generic-ioc` or `--defs` open an untitled
instance using those entities; Save asks where to write it.
