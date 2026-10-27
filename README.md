# dls-ibek-yaml-editor

A desktop editor for ibek `ioc.yaml` files, with a table layout similar to
[xeb](https://github.com/DiamondLightSource/iocbuilder).

Open an IOC service directory, choose an entity type, and edit its parameters.
The editor gets the available entity types from the service's generic IOC
image. It reads the registry directly; Docker and Podman are not needed.

## Quick start

Requires Python 3.13 or later and a desktop environment that can run Qt.

```sh
uvx dls-ibek-yaml-editor services/fe99i-mo-ioc-01
```

Or install it into a Python environment:

```sh
pip install dls-ibek-yaml-editor
dls-ibek-yaml-editor services/fe99i-mo-ioc-01
```

The target can be a service directory, its `config` directory, or an
`ioc.yaml` file. A service normally contains:

```text
fe99i-mo-ioc-01/
├── values.yaml          # selects the generic IOC image
└── config/
    ├── ioc.yaml         # the file you edit
    └── *.ibek.support.yaml  # optional local entity definitions
```

Services using `compose.yml` or `compose.yaml` are also recognised.
If a service has no `config/ioc.yaml`, the editor starts a new document and
creates it when you save. New documents get a schema header when a schema URL
can be derived from the image reference.

## Edit and save

1. Select a type in the list on the left to see its entities as table rows.
2. Use **Components** to add a type, then **Edit > Insert Row** to add an entity.
3. Edit the parameter cells. Hover over a column heading for its description.
4. Use **Tools > Validate** (`F5`) to check the document with ibek.
   Editing clears results from the previous validation; press `F5` to recheck.
5. Save with `Ctrl+S`. Saving validates again; if there are problems, you can
   cancel or choose **Save anyway**.

| In the table | Meaning |
| --- | --- |
| Grey italic value | A default; it is not written unless you set it explicitly |
| Bold column heading | A required parameter |
| Red empty cell | A required value is missing |
| Orange cell or row heading | A problem found during validation |
| **X** in the first column | The entity is commented out |

Double-click the **X** cell to toggle it, or clear it to enable the entity.
Commented entities remain editable, but validation and reference choices
exclude them. Types missing from the loaded definitions have read-only cells.

Enums and booleans use editable drop-downs, so Jinja values can be retained. Object-reference drop-downs offer IDs from
active entities in the document.

### Useful shortcuts

| Action | Shortcut |
| --- | --- |
| Insert row above / below | `Ctrl+I` / `Ctrl+U` |
| Cut / copy / paste | `Ctrl+X` / `Ctrl+C` / `Ctrl+V` |
| Clear selected cells | `Ctrl+D` or `Delete` |
| Fill selected cells | `Ctrl+L` |
| Fill and increment the last number | `Ctrl+R` |
| Undo / redo | `Ctrl+Z` / `Ctrl+Shift+Z` or `Ctrl+Y` |
| Validate | `F5` |
| Save | `Ctrl+S` |

Fill copies the top selected cell down each selected column, or the leftmost
cell across a single selected row. Incrementing preserves number padding:
`MOT.APTR03X` becomes `MOT.APTR04X`, `MOT.APTR05X`, and so on.
Clipboard data uses quoted tab-separated text for spreadsheets and a lossless
internal format between editor tables. Backslashes are literal. Dictionary and
list parameters accept YAML or JSON in the cell. Clearing a cell removes its
key; existing null and empty-string values are preserved until edited.

### Entity order and file formatting

Entity order is startup order. Drag a type in the left list to move its whole
table, or drag a row heading to move an entity within a table. The
**Components > Move Table Up/Down** commands also move whole tables.

Opening a file with interleaved types groups them by first appearance. This
changes startup order, is shown as an undoable edit, and is only written when
you save. Use `Ctrl+Z` to restore the original order. An object must be defined
before another entity references its ID; validation reports these forward
references. Plain string links, such as some `port` parameters, are not checked.

The editor uses ruamel.yaml to retain comments, quotes and Jinja text. It
serialises the whole document, so arbitrary formatting is not guaranteed to
remain identical. The tests check exact preservation for the bundled example.
Commented rows remain disabled when indentation is normalised. Invalid entity
collections are rejected when opening a file, rather than replaced with an
empty document. Saving replaces the file only after a complete temporary copy
has been written. Existing symlinks are followed and file permissions retained;
new files are private to the current user (0600).

## Choose where definitions come from

Usually the image in the service configuration is enough. To override it:

| Option | Source |
| --- | --- |
| `--image REF` | A different generic IOC image |
| `--generic-ioc PATH` | A local checkout: its Dockerfile's support modules plus definitions fetched from its base runtime image |
| `--defs DIR` | The directory's `*.ibek.support.yaml` files; no registry access |

If combined, `--defs` takes priority over `--generic-ioc`, which takes priority
over the image. The editor then adds support files beside the target `ioc.yaml`.
A local definition replaces the **whole module** with the same name. Missing
definition directories and invalid support files are errors; a failed load
leaves the current document open.

For example, to work from local definitions:

```sh
uvx dls-ibek-yaml-editor services/fe99i-mo-ioc-01 --defs ./definitions
```

With no target, any of these source options opens an untitled document. Save
asks for a filename. A checkout whose Git description differs from the pinned
image tag produces a warning.

## Check a file without opening a window

```sh
uvx dls-ibek-yaml-editor services/fe99i-mo-ioc-01 --validate
uvx dls-ibek-yaml-editor services/fe99i-mo-ioc-01 --list-entities
```

These commands do not import Qt or require a display. `--validate` exits with
status `0` when no problems are found and `1` for validation problems or a
missing IOC file. Source options work with both commands.

## Registry access and cache

The first image fetch needs registry access and scans all layers to include
inherited definitions and respect replacements and deletions. Definitions are cached under
`~/.cache/dls-ibek-yaml-editor`; set `DLS_IBEK_YAML_EDITOR_CACHE` to use another
location. Refreshes publish a complete new snapshot and retain older snapshots
so open documents can keep using them. To reclaim space, close the editor and
remove its cache directory; the next run will fetch definitions again. Caches
from older editor versions are rebuilt once.

A cached tag is reused without contacting the registry. Use `--refresh` to
check for an image rebuilt under the same tag. The `latest` tag is always
checked. A failed manifest request falls back to an existing cache and reports
that it is doing so.

For private registries, the client reads inline `auth` entries from Docker's
`config.json` (`~/.docker`, or `DOCKER_CONFIG`). Credential helpers are not
supported. It also supports matching `CI_REGISTRY`, `CI_REGISTRY_USER` and
`CI_REGISTRY_PASSWORD`, including when Docker's config file is absent. A
matching inline Docker credential takes priority.

## Development

- [Architecture](docs/architecture.md): responsibilities and data flow.
- [Development and testing](docs/development.md): setup, checks and coverage.
- `PLAN.md`: the original project brief, where present; it is historical context.

[Source](https://github.com/DiamondLightSource/dls-ibek-yaml-editor) ·
[Releases](https://github.com/DiamondLightSource/dls-ibek-yaml-editor/releases) ·
[Apache 2.0 licence](LICENSE)
