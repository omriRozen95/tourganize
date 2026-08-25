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
| [docs/architecture/decisions.md](docs/architecture/decisions.md) | D1–D12: each decision, its cost, and the feature that reverses it |
| `tourganize/` | The application |
| `config/` | Catalog, prompts and messages — data, not code |
| `tests/` | See [tests/README.md](tests/README.md) for the conventions |

## Status

**F02 has landed: the planning vocabulary.** On top of F01's foundation — typed settings,
structured logging, the `Clock` and `TelemetrySink` ports, the Composition Root, `doctor`,
the container and the import-linter contracts — there is now a Trip Plan made of Plan
Components, whose *types* are declared as data in
[`config/catalog/components.yaml`](config/catalog/components.yaml). Flights, lodging and
ground transport are configuration: a test asserts that grepping `tourganize/` for a shipped
`kind_key` returns nothing at all. Next is
[F03](docs/features/F03-requirement-schemas-and-gap-analysis.md), which says what has to be
known before a component can be planned.

## Getting started

```bash
pip install -e ".[dev]"

tourganize --version
tourganize doctor          # resolved settings, selected adapters, per-port health
tourganize catalog show    # the declared Component Kinds, weights and dependencies
tourganize catalog validate # exit 0, or exit 3 naming every problem in the catalog
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
| `TOURGANIZE_DATA_DIR` | Writable state (sessions, exports, indexes) | `./var` |
| `TOURGANIZE_SECRETS_FILE` | Optional `KEY=value` file, merged *under* the environment | unset |
| `TOURGANIZE_TELEMETRY_SINK` | `null` or `jsonl` | `jsonl` |
| `TOURGANIZE_TELEMETRY_PATH` | Where the JSONL sink writes | `${TOURGANIZE_DATA_DIR}/telemetry.jsonl` |

A `TOURGANIZE_*` key ending in `_KEY`, `_API_KEY`, `_TOKEN`, `_SECRET`, `_PASSWORD` or
`_CREDENTIALS` is treated as a secret: it is wrapped in `SecretValue`, which redacts in
`repr`, `str` and `format`, so it cannot reach a log line or `doctor` output by accident.

A secrets file may only set `TOURGANIZE_*` keys. A key without the prefix is refused with a
`ConfigurationError` naming it, rather than ignored — a secret believed to be loaded is worse
than one that is plainly missing.

## License

MIT — see [LICENSE](LICENSE).
