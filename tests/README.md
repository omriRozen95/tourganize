# Tests

Five directories, one purpose each:

| Directory | What lives here | Introduced by |
|---|---|---|
| `unit/` | One module or one rule at a time, no I/O beyond `tmp_path`. | F01 |
| `contracts/` | One suite per **port**, parametrised over *every* adapter of that port — fakes included. | F01 |
| `integration/` | The wired application: the CLI as a subprocess, containers, real files. | F01 |
| `conversations/` | Golden Conversations replayed through the Scripted Surface. | F11 |
| `architecture/` | The dependency rules themselves: import boundaries, and proof that the linter catches a violation. | F01 |

## The two conventions that must not drift

**A fake per port, shipped by the feature that introduces the port.** Every port in
`tourganize/ports/` has at least one adapter under `tourganize/adapters/<area>/fake/` (or a
named equivalent such as `telemetry/null/`) that lands in the *same* feature as the port.
No feature is ever blocked on a GPU, an API key or a subscription, because the fake is
always available — and fixtures and fakes stay the test default forever, even after live
providers exist.

**Every fake is exercised by a contract test.** A contract suite is written against the
port, never against an adapter: it is parametrised over all known adapters and asserts only
what the port promises. A new adapter is *done* when that suite passes **unmodified** —
if the suite has to be edited to accommodate an adapter, the adapter is wrong or the port's
contract has changed, and the second case needs an entry in
`docs/architecture/decisions.md`.

A corollary that has bitten every project that skipped it: **a fake's shape may never
differ from the real adapter's.** Same fields, same errors, same ordering guarantees.

## Fixtures

`tests/conftest.py` provides:

- `settings_factory(**overrides)` — `Settings` pointed at the test's `tmp_path`, so no test
  writes into the repository. Overrides are `TOURGANIZE_*` keys.
- `frozen_clock` — a `FrozenClock` pinned to a fixed moment.
- `catalog_file` — a valid Component Catalog inside the config directory `settings_factory`
  points at. Requesting it is how a test says "a healthy installation": from F02 on, an
  installation with no catalog fails `doctor`.
- `schema_files` — the Requirement Schemas of that catalog's *enabled* kinds, in the same
  config tree. From F03 on, `catalog validate` and `catalog gaps` need both fixtures; `catalog
  show` and `doctor` still need only the catalog. The disabled kind deliberately has no
  schema — a kind nobody can plan does not need one.
- `option_factory(option_id, kind_key="alpha", *, price=None, **facts)` — a `PlanOption` with
  plausible Provenance, so a test names only what it is about.
- `write_catalog(config_dir, text=SAMPLE_CATALOG)`, `write_schemas(config_dir,
  schemas=SAMPLE_SCHEMAS)` and their sample constants are plain functions, imported as
  `from conftest import write_catalog` by the tests that need a *broken* catalog or schema.
  `tests` is on `pythonpath` in `pyproject.toml` so that import does not depend on
  pytest's import mode; the same mechanism is what lets `tests/architecture` import
  `boundaries`.

Catalog fixtures use neutral `kind_key`s — `alpha`, `beta`, `gamma` — rather than the shipped
travel topics. A test about the machinery should not have to name a topic, and it keeps the
rule that no topic string appears in `tourganize/` easy to see.

## Running

```bash
pytest                                    # everything, with coverage
pytest tests/unit                         # one directory
pytest tests/architecture                 # the dependency rules
pytest -k telemetry                        # one topic
```

`tests/architecture/test_import_linter_enforcement.py` shells out to `lint-imports`; it
skips itself when import-linter is not installed, and the AST checks in
`test_import_boundaries.py` enforce the same rules with no external tool.
