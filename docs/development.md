# Development and testing

Use Python 3.13 or later. Run commands from the repository root.

## Set up an isolated environment

Use a separate environment when the existing `.venv` must be preserved:

```sh
export UV_PROJECT_ENVIRONMENT=/tmp/dls-ibek-yaml-editor-dev
uv sync --locked --group dev
```

`uv sync` without this variable uses the project's `.venv`. The original
`PLAN.md` requests that the existing `.venv` is not modified or deleted during
testing.

Launch against the bundled example without contacting a registry:

```sh
uv run --locked dls-ibek-yaml-editor \
  tests/data/fe99i-mo-ioc-01 --defs tests/data/defs
```

Copy the example to a temporary directory first if you intend to save edits.

## Run the checks

```sh
uv run --locked pytest -rs --cov=dls_ibek_yaml_editor \
  --cov-branch --cov-report=term-missing
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv run --locked pyright --pythonpath "$UV_PROJECT_ENVIRONMENT/bin/python" src tests
```

The GUI tests select Qt's `offscreen` platform automatically. This avoids a
visible desktop, but Qt's native libraries are still required. If an import
fails (for example, missing `libGL.so.1`), the **whole GUI test module skips**.
Always inspect the skip summary; a passing core run is not a GUI check. CI
sets `DLS_IBEK_YAML_EDITOR_REQUIRE_GUI=1`, which turns a failed Qt import into
an error instead of skipping the GUI suite. You can set it locally too.

On Ubuntu, the native libraries for offscreen testing can be installed with:

```sh
./scripts/install-qt-test-deps.sh
```

Inside claude-sandbox, run that command from the outer container.

The configured tox environments run tests with coverage, type checking and
pre-commit. CI requests Python 3.13 and 3.14 on Ubuntu. Pre-commit includes
formatting fixes and a `uv sync` hook, so it can modify files or an environment;
the commands above are preferable for a review that should preserve them.

## What the tests cover

| Test file | Main coverage |
| --- | --- |
| `test_catalogue.py` | Types, defaults, module replacement and checkout discovery |
| `test_document.py` | Example round trips, small edits, ordering, commented entities and error positions |
| `test_service.py` | Target discovery, new documents and local definitions |
| `test_validate.py` | ibek validation and global-state cleanup |
| `test_file_io.py` | Safe replacement, permissions, symlinks and failed saves |
| `test_table_text.py` | Lossless clipboard text and numeric fill |
| `test_image.py` | Image references, schema URLs, simulated layers, cache reuse and offline fallback |
| `test_cli.py` | Version, entity listing and validation exit status |
| `test_gui.py` | Tables, edits, undo, save, fill, paste, ordering, references and commented rows |

Tests copy service fixtures to temporary directories and isolate the image
cache. Registry unit tests use tar archives built in memory. Layer tests replace
`Registry.get`; separate transport tests exercise credential lookup, challenges
and redirect-header handling with simulated HTTP responses.

The optional live test reads the image pinned in `tests/conftest.py`:

```sh
DLS_IBEK_YAML_EDITOR_LIVE=1 uv run --locked pytest tests/test_image.py::test_live_registry
```

It needs access to that registry. The normal suite leaves it disabled.

## Adding useful tests

Prefer observable outcomes: the saved YAML, undo restoring the original
content, the selected problem row, or the CLI's exit status. Use small fixtures
for edge cases and retain the full IOC fixture for realistic integration.

Regression tests now cover the following areas; extend them when changing
related behaviour:

- Different YAML indentation, malformed entity collections, nested values,
  anchors, multiline strings and comment placement.
- Failed reloads and saves; Cancel, Discard and Save dialog outcomes.
- Real delegate commits, including Jinja values in boolean and enum cells.
- Credential lookup, HTTP challenges, cache refresh failures and definitions
  spanning image layers.
- Validation followed by insertion, deletion or reordering before navigating
  to a problem.

Coverage is a guide to unexercised code; it does not establish that YAML or
undo behaviour is correct for untested inputs. Keep both focused regression
tests and the original IOC fixture when extending the supported inputs.
