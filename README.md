# Tourganize

A conversational trip-planning assistant. The traveller says what they want in English or
Hebrew; Tourganize interviews them for what is missing, plans the trip **one Plan Component
at a time**, offers a short slate of options per component, lets them choose or push back,
and finally exports a written plan.

## Where things are

| Path | What it is |
|---|---|
| [docs/roadmap.md](docs/roadmap.md) | The 25 features, their dependency graph and the build order. **Read this first.** |
| [docs/architecture/overview.md](docs/architecture/overview.md) | Bounded contexts, package layout, ports, the shape of one turn |
| [docs/architecture/glossary.md](docs/architecture/glossary.md) | The naming authority |
| [docs/architecture/decisions.md](docs/architecture/decisions.md) | D1–D19: each decision, its cost, and the feature that reverses it |
| `tourganize/` | The application |
| `config/` | Catalog, prompts and messages — data, not code |
| `fixtures/` | Recorded option data, cassettes and golden conversations — see [fixtures/README.md](fixtures/README.md) |
| `tests/` | See [tests/README.md](tests/README.md) for the conventions |

## Status

**F06 has landed: the conversation now shows real options.** On top of F01's foundation, F02's Trip
Plan, F03's Requirement Schemas, F04's Planning Agenda and F05's **Dialogue Director**, the slates the
Director presents are built for real. Behind its `OptionSlatePlanner` seam sits a **Planning Service**
that assembles one Option Query per round, calls the Option Sources registered for that Component Kind
— serially, each within a time budget — merges and de-duplicates what comes back, marks every option
with the optional filters it fails, ranks it and truncates to the slate size. The sources are **Fixture
Providers**: one generic provider reading [`fixtures/options/<kind_key>/*.json`](fixtures/README.md),
never one class per topic, and per [D9](docs/architecture/decisions.md) they stay the test default
forever. F17's MCP-backed source and F24's live providers implement the identical port and are finished
when the same contract suite passes over them.

Three properties are worth stating. **Sourcing degrades, it never dies:** one source failing is a
diagnostic and a source skipped, only *every* source failing is an error, and even that becomes an Act
rather than the end of a conversation. **Optional filters are soft:** a traveller who says "under €150"
is still shown the €160 room — below the ones under €150, and *marked* — because an empty slate answers
nothing; `TOURGANIZE_OPTION_FILTER_STRICT=true` is there for an installation that disagrees. **The same
query yields a byte-identical slate** in any process, which is what F11's replay will rest on.

The Dialogue Director itself did not change: it resolves every blocking question *before* anything is
sourced — one question per turn — presents an Option Slate, accepts a choice **or** a refinement and
re-plans the same component any number of times, bundles the optional filters alongside the first slate
and never asks them again, offers to plan the Component Kinds the traveller never mentioned once the
ones they did are settled, and closes the session on their answer. It contains all the control flow and
**no wording**: it emits **Assistant Acts** — structured, locale-neutral intents to communicate — which
F07 will draw and F08/F10 will phrase. Turns come in through the `TurnInterpreter` port, so a
deterministic keyword interpreter reading
[`config/interpretation/keywords.en.yaml`](config/interpretation/keywords.en.yaml) drives it today and
F08 swaps in a model-backed one with `TOURGANIZE_INTERPRETER`. There is still no surface, so the
conversation is driven from the tests until F07 wires `tourganize chat`. Next is
[F07](docs/features/F07-presentation-surface-and-terminal-shell.md), the presentation surface.

## Getting started

```bash
pip install -e ".[dev]"

tourganize --version
tourganize doctor          # resolved settings, selected adapters, per-port health
tourganize catalog show    # the declared Component Kinds, weights and dependencies
tourganize catalog validate # exit 0, or exit 3 naming every problem in the catalog or a schema
tourganize catalog gaps --kind lodging                      # what is still blocking planning
tourganize catalog gaps --kind lodging \
  --set '{"place": "Paris", "date_range": "2026-10-23/2026-10-28"}'   # is_plannable: true
tourganize catalog agenda --mentioned lodging               # what would be planned next, and why
tourganize options search --kind lodging \
  --set '{"place": "Paris", "date_range": "2026-10-23/2026-10-28"}'  # a real Option Slate
tourganize chat            # exits 2 until F07 implements it — the Director has no surface yet
```

In a container, CPU only:

```bash
docker compose --profile dev-cpu build
docker compose --profile dev-cpu run --rm app tourganize doctor
```

## Checks

```bash
scripts/check                         # all four gates at once, and every verdict in one report
scripts/check tests/unit/test_agenda.py   # pytest narrowed; the other gates still run
scripts/check --cov                   # …plus the coverage report CI prints
```

The gates are independent, so running them one at a time only buys four round-trips and a report
that stops at the first failure. Individually they are still:

```bash
ruff check . && ruff format --check .
mypy --strict tourganize
lint-imports                          # the DDD boundary; a failure here is a design regression
pytest
```

Coverage is not in `addopts`: nothing gates on it, and it roughly doubles the cost of the
single-file runs that dominate an edit/check loop. CI asks for it explicitly, and so does
`scripts/check --cov`.

## Configuration

Every setting is a `TOURGANIZE_*` environment variable with a documented default, loaded
only through `Settings.from_env`. `tourganize doctor` prints the resolved values, with
secrets redacted, and reports any `TOURGANIZE_*` key it does not recognise.

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_ENV` | Runtime profile: `dev`, `test`, `prod` | `dev` |
| `TOURGANIZE_LOG_LEVEL` | Python log level | `INFO` |
| `TOURGANIZE_LOG_FORMAT` | `json` or `human` | `human` in dev, `json` otherwise |
| `TOURGANIZE_CONFIG_DIR` | Root of `catalog/`, `prompts/`, `messages/` | `./config` |
| `TOURGANIZE_CATALOG_PATH` | The Component Catalog file | `${TOURGANIZE_CONFIG_DIR}/catalog/components.yaml` |
| `TOURGANIZE_SCHEMA_DIR` | Directory of Requirement Schema files | `${TOURGANIZE_CONFIG_DIR}/catalog/schemas` |
| `TOURGANIZE_DATA_DIR` | Writable state (sessions, exports, indexes) | `./var` |
| `TOURGANIZE_SECRETS_FILE` | Optional `KEY=value` file, merged *under* the environment | unset |
| `TOURGANIZE_TELEMETRY_SINK` | `null` or `jsonl` | `jsonl` |
| `TOURGANIZE_TELEMETRY_PATH` | Where the JSONL sink writes | `${TOURGANIZE_DATA_DIR}/telemetry.jsonl` |
| `TOURGANIZE_PRIORITY_POLICY` | Which Priority Policy orders the Agenda: `weighted` or `fixed` | `weighted` |
| `TOURGANIZE_AGENDA_FAILURE_SKIP` | Sourcing failures in a row before a Component Kind is skipped | `2` |
| `TOURGANIZE_DIALOGUE_MAX_REASKS` | Asks on one Blocking Rule before the Director gives up on it | `3` |
| `TOURGANIZE_DIALOGUE_OPTIONAL_ASK_LIMIT` | Optional fields bundled into one `ask_optional` Act | `2` |
| `TOURGANIZE_DIALOGUE_OFFER_BATCH` | Unmentioned Component Kinds named in one `offer_unmentioned` Act | `2` |
| `TOURGANIZE_INTERPRETER` | Which Turn Interpreter is wired: `keyword`, or `model` from F08 | `keyword` |
| `TOURGANIZE_KEYWORD_CONFIG_DIR` | The keyword interpreter's phrase tables | `${TOURGANIZE_CONFIG_DIR}/interpretation` |

A `TOURGANIZE_*` key ending in `_KEY`, `_API_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD` or
`_CREDENTIALS` is treated as a secret: it is wrapped in `SecretValue`, which redacts in
`repr`, `str` and `format`, so it cannot reach a log line or `doctor` output by accident.

A secrets file may only set `TOURGANIZE_*` keys. A key without the prefix is refused with a
`ConfigurationError` naming it, rather than ignored — a secret believed to be loaded is worse
than one that is plainly missing.

## License

MIT — see [LICENSE](LICENSE).
