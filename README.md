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

**F07 has landed: there is something to talk to.** `tourganize chat` runs the whole conversation —
F01's foundation, F02's Trip Plan, F03's Requirement Schemas, F04's Planning Agenda, F05's **Dialogue
Director** and F06's real fixture-backed Option Slates, in front of a person for the first time. Three
pieces close the walking skeleton. The **Presentation Surface** port is the traveller-facing edge:
`show` an Assistant Act, `next_turn` for the next thing they typed — `None` meaning they left — and
`notify` for status that is never part of the transcript. Two adapters implement it: a **Terminal
Surface** (Textual: a scrolling transcript, an input line disabled while the Director is working, and
`Ctrl+C` as a clean close) and a **Scripted Surface** that replays a list of lines headlessly and
records every Act it was shown, which is the cheapest integration test there is and the backbone of
F11's Golden Conversations. Between them and the Director sits the **Session Runner**: twenty lines,
the only place the two ports meet, and the reason neither knows the other exists.

The words come from a **Message Catalogue**. The Director still emits Assistant Acts and no prose; the
**Act Renderer** is the one place an Act becomes text, drawing every sentence from
[`config/messages/<locale>.yaml`](config/messages/en.yaml) and every option column from a per-kind
**Display Profile** in `config/messages/display.<locale>.yaml`. A message key nobody declares renders a
visible `⟪missing:the.key⟫` and logs — never a crash, never a silent blank — and an unconfigured fourth
Component Kind still renders, from the facts its options declare. `en.yaml` and `he.yaml` both ship, so
the RTL path exists from day one and `direction` is plumbed end to end; making Hebrew *look* right is
F10's, and everything here is provisional by design.

The demo is a file, not a paragraph:
`tourganize chat --script fixtures/conversations/paris.txt` replays the Phase 1 conversation headlessly
and exits 0, which is what CI gates on, since a CI runner has no terminal. Next is
[F08](docs/features/F08-llm-gateway-and-prompt-library.md), the LLM Gateway, which replaces the keyword
Turn Interpreter without moving a single line of the state machine.

## Getting started

```bash
pip install -e ".[dev,terminal]"   # `terminal` is what the default Presentation Surface needs

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

tourganize chat                                       # the whole conversation, in a terminal
tourganize chat --locale he                           # …in Hebrew (F10 makes it *look* right)
tourganize chat --script fixtures/conversations/paris.txt   # the same session, headless
echo "find me a hotel in Paris" | TOURGANIZE_SURFACE=scripted tourganize chat
```

`chat` exits `0` on a normal close, `1` on an error nobody expected — naming the session id, so
the turn-by-turn record can be found in the telemetry log — `2` on an unusable invocation and `3`
on a configuration problem.

In a container, CPU only:

```bash
docker compose --profile dev-cpu build
docker compose --profile dev-cpu run --rm app tourganize doctor

# The Phase 1 demo. `run` allocates a TTY, which is what the Terminal Surface needs; add `-T`
# for the headless paths (`--script`, `doctor`, `catalog`, `options`), which is how CI drives
# this image, since a CI runner has no terminal to give.
docker compose --profile dev-cpu run --rm app tourganize chat
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
| `TOURGANIZE_OPTION_SOURCE_PROFILE` | Which Option Sources are wired, globally or `kind=profile` per Component Kind | `fixture` |
| `TOURGANIZE_FIXTURE_DIR` | Root of the Fixture Providers' recorded option data | `./fixtures/options` |
| `TOURGANIZE_SLATE_SIZE` | Plan Options presented per round | `3` |
| `TOURGANIZE_OPTION_FILTER_STRICT` | Optional filters discard rather than demote and annotate | `false` |
| `TOURGANIZE_OPTION_SOURCE_TIMEOUT_SECONDS` | Time budget per Option Source, per query | `10` |
| `TOURGANIZE_SURFACE` | Which Presentation Surface `chat` runs: `terminal` or `scripted` | `terminal` |
| `TOURGANIZE_MESSAGE_DIR` | Message Catalogue and Display Profiles | `${TOURGANIZE_CONFIG_DIR}/messages` |
| `TOURGANIZE_DEFAULT_LOCALE` | Locale Tag to talk in when nothing is detected | `en` |
| `TOURGANIZE_SUPPORTED_LOCALES` | Comma-separated Locale Tags the catalogue ships | `en,he` |

A `TOURGANIZE_*` key ending in `_KEY`, `_API_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD` or
`_CREDENTIALS` is treated as a secret: it is wrapped in `SecretValue`, which redacts in
`repr`, `str` and `format`, so it cannot reach a log line or `doctor` output by accident.

A secrets file may only set `TOURGANIZE_*` keys. A key without the prefix is refused with a
`ConfigurationError` naming it, rather than ignored — a secret believed to be loaded is worse
than one that is plainly missing.

## License

MIT — see [LICENSE](LICENSE).
