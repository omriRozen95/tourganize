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
| [docs/architecture/decisions.md](docs/architecture/decisions.md) | D1–D16: each decision, its cost, and the feature that reverses it |
| `tourganize/` | The application |
| `config/` | Catalog, prompts and messages — data, not code |
| `tests/` | See [tests/README.md](tests/README.md) for the conventions |

## Status

**F04 has landed: what to plan next.** On top of F01's foundation, F02's Trip Plan and F03's
Requirement Schemas, a Trip Plan now yields a **Planning Agenda**: the Component Kinds the
traveller raised, first and always (the client's Mentioned-First Rule, which lives in
`build_agenda` and is not configurable), then the ones they did not — each band ordered by a
**replaceable Priority Policy** built from the weights and Outcome Dependencies declared in
[`config/catalog/components.yaml`](config/catalog/components.yaml). Outcome Dependencies are
soft: a traveller who wants only a hotel is never held waiting on flights they never mentioned, and
the ordering they do imply is applied by `build_agenda`, so no choice of policy can get it wrong.
`tourganize catalog agenda --mentioned lodging` prints the order, the bands and the reason codes;
`TOURGANIZE_PRIORITY_POLICY=fixed` re-orders it with no code change. The importance metric the
client deferred now has a home to be defined in. Next is
[F05](docs/features/F05-dialogue-director-and-session-lifecycle.md), the dialogue director.

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
tourganize chat            # exits 2 until F07 implements it
```

In a container, CPU only:

```bash
docker compose --profile dev-cpu build
docker compose --profile dev-cpu run --rm app tourganize doctor
```

## Checks

```bash
ruff check . && ruff format --check .
mypy --strict tourganize
lint-imports                          # the DDD boundary; a failure here is a design regression
pytest
```

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

A `TOURGANIZE_*` key ending in `_KEY`, `_API_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD` or
`_CREDENTIALS` is treated as a secret: it is wrapped in `SecretValue`, which redacts in
`repr`, `str` and `format`, so it cannot reach a log line or `doctor` output by accident.

A secrets file may only set `TOURGANIZE_*` keys. A key without the prefix is refused with a
`ConfigurationError` naming it, rather than ignored — a secret believed to be loaded is worse
than one that is plainly missing.

## License

MIT — see [LICENSE](LICENSE).
