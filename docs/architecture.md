# How the code is organised

This page is for anyone changing the tool. The [README](../README.md) covers
using it.

## Module map

```mermaid
flowchart TD
    main["__main__<br/>typer CLI"]
    subgraph gui ["gui/ (PySide6)"]
        window["main_window<br/>MainWindow, TableView"]
        table["table_model<br/>EntityTableModel"]
        delegates["delegates<br/>CellDelegate"]
        commands["commands<br/>DocumentModel + undo commands"]
    end
    subgraph core ["core (no Qt)"]
        service["service<br/>resolve, load_catalogue, open_document"]
        validate["validate<br/>ibek's IOC model"]
        document["document<br/>round-trip ioc.yaml"]
        catalogue["catalogue<br/>entity types and parameters"]
        image["image<br/>registry pull + cache"]
    end
    ibek[("ibek")]

    main --> service & validate
    main -. "imported only when the GUI starts" .-> window
    window --> table & delegates & commands & service & validate
    delegates --> table
    table --> commands
    commands --> document
    service --> catalogue & document & image
    validate --> document & catalogue
    catalogue --> image
    catalogue & validate & image --> ibek
```

The rule that matters: **nothing outside `gui/` imports Qt.** That is why
`--list-entities`, `--validate` and most of the tests run with no display or
libGL. `__main__` imports the GUI inside the function that starts it. If Qt
can't load, it reports that and exits.

| Module | Job |
|---|---|
| `image` | Turns an image reference into a local folder of `*.ibek.support.yaml`. It talks to the registry over the OCI API, using only the standard library, and keeps a cache. |
| `catalogue` | Parses support YAML with ibek's own `Support` model into `EntityType` and `Param` objects. Its builders are `from_defs`, `from_image` and `from_generic_ioc`. |
| `document` | Holds the `ioc.yaml` in ruamel round-trip form and provides the edits (set, insert, remove, move, regroup, comment out). |
| `service` | Ties a service directory to its image, its catalogue and its document. |
| `validate` | Runs the document through ibek's generated pydantic IOC model and maps errors back to entities and fields. |
| `gui/commands` | `DocumentModel`: the only way the GUI changes a document, through `QUndoCommand`s. |
| `gui/table_model` | Shows one entity type as a Qt table. |
| `gui/delegates` | The cell editors: combos for enums, bools and object references. |
| `gui/main_window` | The xeb-style window, its menus, the clipboard, Fill and Fill and Increment. |

## Start-up

```mermaid
sequenceDiagram
    actor User
    participant CLI as __main__
    participant W as MainWindow
    participant S as service
    participant C as catalogue
    participant I as image
    participant R as registry

    User->>CLI: dls-ibek-yaml-editor services/fe99i-mo-ioc-01
    CLI->>W: run(target, Sources)
    W->>W: show(), then load() on the first event-loop turn
    W->>S: resolve(target)
    S-->>W: Instance(ioc_yaml, service_dir, image from values.yaml)
    W->>S: load_catalogue(instance, sources)
    alt --defs DIR
        S->>C: from_defs(DIR)
    else --generic-ioc PATH
        S->>C: from_generic_ioc(PATH)
        C->>C: parse the Dockerfile (DEVELOPER image, COPY + ansible.sh folders)
        C->>I: fetch_ibek_defs(base runtime image)
    else default, or --image
        S->>C: from_image(image)
        C->>I: fetch_ibek_defs(image)
    end
    alt tag cached and not --refresh
        I-->>C: cache folder (no network)
    else
        I->>R: manifest (401, then anonymous token, then retry)
        I->>R: config blob, history picks the COPY /assets layer
        I->>R: stream the layer and stop once /epics/ibek-defs has passed
        I-->>C: new cache folder, keyed by manifest digest
    end
    S->>S: add the instance's config/*.ibek.support.yaml
    W->>S: open_document(instance)
    S-->>W: Document (or a new one with the $schema header)
    W->>W: DocumentModel, Components menu, type list, tables
```

Points to know:

- **Entities come from the image, not the checkout.** A generic IOC
  inherits modules from its base image. For example, iocStats in
  ioc-dlsmotorplc comes from `ioc-asyn-developer`. Those modules are in the
  runtime image's `/epics/ibek-defs` but not in the generic IOC's
  Dockerfile. `--generic-ioc` handles this by pulling the base image for
  them.
- **`/epics/ibek-defs/*.yaml` are symlinks** into
  `/epics/generic-source/...`, which comes earlier in the same layer.
  `_scan_layer` keeps every support YAML it passes, then resolves the links.
- **A cached tag skips the network.** Release tags don't change, and some
  workstations can't reach the registry. `--refresh` asks the registry
  anyway, and a `latest` tag is always checked.

## How an edit flows

```mermaid
classDiagram
    class Document {
        data: CommentedMap
        entities
        set(entity, key, value)
        insert()
        remove()
        move()
        reorder()
        is_commented()
        set_commented()
        dumps()
        save()
    }
    class DocumentModel {
        document: Document
        catalogue: Catalogue
        stack: QUndoStack
        type_order: list
        valueChanged(entity, key)
        structureChanged()
        set_text()
        insert()
        remove()
        move()
        set_type_order()
        remove_table()
    }
    class EntityTableModel {
        type_name
        rows: entities of that type
        columns: X + parameters
        data()
        setData()
    }
    class QUndoCommand {
        redo()
        undo()
    }
    class MainWindow
    MainWindow --> DocumentModel
    MainWindow --> "one per type" EntityTableModel
    EntityTableModel --> DocumentModel
    DocumentModel --> Document
    DocumentModel ..> QUndoCommand : pushes
    QUndoCommand <|-- SetValue
    QUndoCommand <|-- SetCommented
    QUndoCommand <|-- InsertEntity
    QUndoCommand <|-- RemoveEntity
    QUndoCommand <|-- MoveEntity
    QUndoCommand <|-- Reorder
    QUndoCommand <|-- SetDescription
```

```mermaid
sequenceDiagram
    participant V as TableView / CellDelegate
    participant T as EntityTableModel
    participant M as DocumentModel
    participant U as QUndoStack
    participant D as Document

    V->>T: setData(index, "1e-05")
    T->>M: set_text(entity, "MRES", "1e-05")
    M->>M: convert the text with the parameter's type; ignore if unchanged
    M->>U: push(SetValue)
    U->>D: SetValue.redo() → set(entity, key, value)
    U-->>M: valueChanged(entity, key)
    M-->>T: valueChanged → dataChanged for that cell
```

Rules that keep this working:

- **Every change goes through `DocumentModel` and the undo stack.** Undo,
  redo and the "modified" title mark all depend on it. Fill, paste and
  clear are undo macros of single-cell edits.
- **Entities are found by identity, never by equality.** Two new entities
  of the same type are equal dicts. Use `row_of`, `index_of` and `is`, never
  `in` or `.index()` on lists of entities.
- **Changes to the order** emit `structureChanged`, and every table rebuilds.
  Drag and drop handlers schedule the change with `QTimer.singleShot`,
  because changing the model while Qt is still handling its own drop
  signal is unsafe.
- **Grouped by type, as in xeb.** The type list order *is* the file order,
  so moving a table regroups `entities` (`Reorder`). ibek needs an entity to
  come before anything that refers to its id. Validation catches an `object`
  reference that comes too early. Plain string links such as `port:` are not
  checked, by ibek or here.

## Loading and saving `ioc.yaml`

```mermaid
flowchart LR
    subgraph load ["Document(text)"]
        A[text] --> B["uncomment_entities<br/>'# - type:' blocks that parse<br/>as one entity become real items"]
        B --> C["ruamel round-trip load<br/>preserve_quotes, width 4096"]
        C --> D["entities + the positions<br/>and prefixes of commented ones"]
    end
    subgraph save ["dumps()"]
        E["_normalise_separators<br/>blank line after each entity,<br/>comments left alone"] --> F[ruamel dump]
        F --> G["comment_entities<br/>re-comment X rows with<br/>their original prefix"]
        G --> H[text]
    end
    D -. edits .-> E
```

- **An unedited file saves byte-identical,** and tests check that for
  fe99i. That's the basis for "saving changes only the lines you edited",
  so check it whenever this code changes.
- **Blank lines between entities** are stored by ruamel as a comment token
  on the *last key* of the entity before them. Adding a key at the end or
  moving an entity would move that blank line, so `_normalise_separators`
  puts it back on each entity's last key.
- **Commented-out entities are YAML comments, not `entity_enabled: false`.**
  That matches xeb and how people comment out entities by hand. While the
  file is open they are real entities, so they can still be edited.
  `plain()`, which feeds validation, and `ids()`, which feeds the reference
  drop-downs, both leave them out.

## Validation

`validate.validate` builds ibek's models from the catalogue's support files
(`EntityFactory.make_entity_models`, then `IocFactory.make_ioc_model`). It
then validates `document.plain()` and turns each pydantic error location
`entities, <n>, <type>, <field>` into a `Problem` for that entity and field.
Because commented entities are left out, `<n>` is mapped back to the
entity's position in the whole file. ibek prints while it works and gives a
pydantic deprecation warning, so both are silenced. ibek's global id
registry is cleared before and after each run.

## Packaging constraints

- **Python ≥ 3.13,** because ibek ≥ 4.7 requires it.
- **`pyside6-essentials` < 6.10.** Wheels from 6.10 on need glibc 2.34, and
  RHEL 8 has 2.28.
- **No docker or podman.** The registry is read with `urllib` and
  `truststore`, which uses the OS certificate store, as ibek does.

## Tests

- **Data:** `tests/data` holds a copy of fe99i, a snapshot of the image's
  support YAML and a Dockerfile.
- **Registry tests** use a fake registry made from in-memory tar layers.
  `DLS_IBEK_YAML_EDITOR_LIVE=1` adds one test against the real registry.
- **GUI tests** (`tests/test_gui.py`) run with `QT_QPA_PLATFORM=offscreen`,
  and skip themselves when Qt can't load.
