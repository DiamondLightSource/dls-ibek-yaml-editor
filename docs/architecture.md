# Architecture

The editor has a Python core and a Qt interface. The core discovers entity
definitions, loads YAML and validates it. The interface presents tables and
records edits for undo. [The README](../README.md) explains how to use it.

## Main parts

```mermaid
flowchart TD
    CLI["CLI: __main__.py"] --> Service["service.py: locate and open an IOC"]
    CLI --> Window["gui/main_window.py: window and table actions"]
    CLI --> Validation["validate.py: ibek validation"]
    Window --> Service
    Window --> Validation
    Window --> Table["gui/table_model.py and delegates.py"]
    Table --> Commands["gui/commands.py: edits and undo"]
    Commands --> Document["document.py: YAML and entity order"]
    Service --> Document
    Service --> Catalogue["catalogue.py: types and parameters"]
    Catalogue --> Image["image.py: registry and cache"]
    Validation --> Document
    Validation --> Catalogue
```

Arrows show the main dependencies, not every import. **Only `gui/` imports Qt.**
The CLI imports the GUI when starting a window; reporting commands can run
without a display or Qt system libraries.

| Module | Responsibility |
| --- | --- |
| `service.py` | Resolve a target into an `Instance`; choose definitions; open or create a `Document` |
| `catalogue.py` | Parse support files through ibek into `Catalogue`, `EntityType` and `Param` objects |
| `image.py` | Read image manifests and layers; resolve definition symlinks; manage the cache |
| `document.py` | Own the round-trip YAML, entity order and commented state |
| `validate.py` | Build ibek's IOC model and return `Problem` objects |
| `gui/commands.py` | Own the `DocumentModel` and its `QUndoStack`; implement undoable changes |
| `gui/table_model.py` | Present one entity type as rows and parameters as columns |
| `gui/delegates.py` | Create text editors and drop-downs for cells |
| `gui/main_window.py` | Assemble the window; handle files, menus, clipboard, fill and problem navigation |

## Opening a document

```mermaid
flowchart TD
    Target["Service directory, config directory or IOC file"] --> Instance["Instance: file path, service directory, image"]
    Instance --> Choice{"Definition source"}
    Choice -->|"--defs"| Folder["Local support files"]
    Choice -->|"--generic-ioc"| Checkout["Checkout files and base runtime image"]
    Choice -->|"--image or service image"| Registry["Image cache or registry"]
    Folder --> Catalogue["Catalogue"]
    Checkout --> Catalogue
    Registry --> Catalogue
    Catalogue --> Local["Overlay support files beside ioc.yaml"]
    Instance --> Document["Load YAML, or create a new document"]
    Local --> Model["DocumentModel"]
    Document --> Model
    Model --> Tables["Tables, type list and undo history"]
```

`load_catalogue()` selects sources in this order: explicit definitions,
checkout, then image. Local support files replace modules with matching names.
Support-file parsing failures become catalogue warnings.

Checkout discovery recognises the project's `ARG DEVELOPER`, `COPY` and
`RUN ansible.sh` conventions. It is a limited parser, not a Dockerfile build
engine. Failure to fetch the base image becomes a warning, allowing a partial
catalogue to open.

The registry client chooses linux/amd64 from a multi-platform index. It tries
layers identified by image history first, then other layers newest first.
It stops at the first layer containing definitions. Within a layer, it retains
support YAML and resolves links from `epics/ibek-defs` to those files.

This is tailored to the expected generic IOC layout. It does **not** reconstruct
an arbitrary container filesystem: definitions split across layers, whiteouts
and links into another layer need additional handling. Cache directories are
keyed by image/tag; metadata records the manifest digest and extracted names.

## Editing and undo

```mermaid
sequenceDiagram
    participant UI as Table or cell editor
    participant Table as EntityTableModel
    participant Model as DocumentModel
    participant Stack as QUndoStack
    participant Doc as Document
    UI->>Table: setData(index, text)
    Table->>Model: set_text(entity, key, text)
    Model->>Model: Convert text using the parameter type
    Model->>Stack: Push a command if changed
    Stack->>Doc: Command.redo() applies the edit
    Stack-->>Table: Command emits valueChanged or structureChanged
    Table-->>UI: Refresh the cell or table
    UI->>Stack: Undo
    Stack->>Doc: Command.undo() restores the previous state
```

Keep these rules when adding features:

- Persistent GUI edits belong on the undo stack. Paste, fill and clear group
  their cell commands into one undo action. Adding an empty table is UI state
  and does not itself change the document.
- Find entity rows **by identity** (`is`, `index_of`, `row_of`). Two new entities
  can be equal dictionaries while representing different rows.
- `Document.entities` determines startup order. `DocumentModel.type_order`
  determines table order; regrouping synchronises them through an undo command.
- Structure changes reset table models. Drag handlers defer changes until Qt
  has finished handling its own move signal.
- The stack's clean marker tracks unsaved edits. Set it only after a successful
  write; clearing it prematurely can lose the unsaved-changes prompt.

## YAML and commented entities

`Document` holds ruamel `CommentedMap` and `CommentedSeq` objects so comments,
quotes and scalar styles can survive editing. Loading temporarily uncomments
recognised `# - type:` blocks and tracks their entities by object identity.
Serialisation dumps the YAML and comments those entities again.

Blank separators are stored in ruamel comment tokens attached to keys.
`_normalise_separators()` adjusts those tokens after inserts and moves. This
couples the document code to ruamel's comment representation; keep it covered
with small before/after fixtures as well as the full IOC example.

The serializer writes the whole document and uses a fixed indentation style.
Exact text preservation is tested for the example fixture, not guaranteed for
all YAML layouts. See [the review](review.md) for confirmed edge cases.

## Validation

`Document.plain()` produces ordinary Python data without commented entities.
Validation builds models using ibek's `EntityFactory` and `IocFactory`, then
maps errors to `Problem(entity, field, message)`. Entity positions are mapped
back to the full document, including commented rows.

ibek's ID registry is cleared before and after instance validation. The
validator sets ibek's IOC name, captures stdout and suppresses deprecation
warnings while invoking ibek. These are process-wide effects: move validation
to concurrent workers only after addressing that shared state.

The Problems panel stores positions from the last validation. Edits do not
currently recompute them; structural changes can make navigation stale.

## Where to make a change

| Change | Start here | Tests to extend |
| --- | --- | --- |
| File discovery or definition precedence | `service.py` | `test_service.py` |
| A new definition source | `catalogue.py`, then `service.py` | `test_catalogue.py` |
| Authentication, layers or cache | `image.py` | `test_image.py` |
| YAML preservation or value conversion | `document.py` | `test_document.py` |
| A new undoable operation | `gui/commands.py` | `test_gui.py` |
| Cell editing or presentation | Table model and delegates | `test_gui.py` |
| Validation mapping | `validate.py` | Currently `test_service.py` and `test_document.py` |

See [development and testing](development.md) for commands.
