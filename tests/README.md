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
- `option_fixture_dir` — a Fixture Provider tree (`<kind_key>/<name>.json`) for the sample catalog's
  enabled kinds, with two places, two currencies and review scores spread wide enough that a filter,
  a ranking and a refinement all visibly do something. A *missing* tree is not a broken installation:
  the Fixture Provider synthesises rather than dead-ending, so this fixture buys recorded data to
  assert on.
- `keyword_files` — the keyword Turn Interpreter's Phrase Tables, in the same config tree. From F05
  on, "a healthy installation" means these too: the `TurnInterpreter` is a wired port, so `doctor`
  probes it by reading a turn, and an installation with no phrases fails that check.
- `message_files` — the Message Catalogue and the Display Profiles, one of each per supported
  locale, in that same config tree. From F07 on, "a healthy installation" means these as well:
  the Act Renderer is wired, so `doctor` loads a catalogue for every locale in
  `TOURGANIZE_SUPPORTED_LOCALES`, and a locale with no catalogue is a misconfigured installation
  rather than a conversation full of `⟪missing:…⟫` markers. Hebrew is present from the first
  surface, so the RTL path is never retrofitted.
- `option_factory(option_id, kind_key="alpha", *, price=None, **facts)` — a `PlanOption` with
  plausible Provenance, so a test names only what it is about.
- `write_catalog(config_dir, text=SAMPLE_CATALOG)`, `write_schemas(config_dir,
  schemas=SAMPLE_SCHEMAS)`, `write_keywords(config_dir, tables={"en": SAMPLE_KEYWORDS})`,
  `write_messages(config_dir, catalogues=SAMPLE_MESSAGES)`, `schemas_dir(config_dir)`,
  `keywords_dir(config_dir)`, `messages_dir(config_dir)`, `keyword_table(*kind_keys)`,
  `message_catalogue(locale, direction, prefix="")` and their
  sample constants are plain functions, imported as `from conftest import write_catalog` by the
  tests that need a *broken* catalog, schema or phrase table. `keyword_table()` is where
  `SAMPLE_KEYWORDS` comes from: a suite whose Component Kinds differ from `SAMPLE_CATALOG`'s asks
  for a table naming its own, rather than copying one and editing the `kinds:` block. `tests` is on `pythonpath` in `pyproject.toml` so that import does not
  depend on pytest's import mode; the same mechanism is what lets `tests/architecture` import
  `boundaries`.
- `schemas_dir(config_dir)` is the documented `TOURGANIZE_SCHEMA_DIR` default. The YAML adapter
  is handed its schema directory and has no fallback of its own, so a test that builds one by
  hand has to say where the schemas are — and should say it the same way everywhere.

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

A test that needs a *whole conversation* rather than a fixture tree reads a transcript out of
`fixtures/conversations/` and replays it through the Scripted Surface — one traveller turn per
line, no expected replies in the file. `tests/integration/test_chat_end_to_end.py` is where F07's
Definition of done is made observable, and where the act-name sequence of the first Golden
Conversation is pinned until F11 stores it beside the transcript.

`tests/architecture/test_import_linter_enforcement.py` shells out to `lint-imports`; it
skips itself when import-linter is not installed, and the AST checks in
`test_import_boundaries.py` enforce the same rules with no external tool.
