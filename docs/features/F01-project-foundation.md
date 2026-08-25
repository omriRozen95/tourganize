# F01 — Project foundation, configuration and container baseline

- **Bounded context:** Platform (cross-cutting)
- **Depends on:** none
- **Unlocks:** F02, F06, F08, F12, F15, F18, F20 — everything
- **Size:** M
- **Status of the codebase when this starts:** empty repository containing `LICENSE`, `.gitignore`,
  `project_demands.md`, `architecture_brief.md` and the `docs/` tree. No Python package, no tests, no
  container.

## Purpose

Give the project a spine: an installable `tourganize` package with the layout the architecture assumes,
typed Settings loaded from the environment, structured logging, a CLI entry point, a runnable
CPU-only container, and CI gates that will fail any later feature that violates the dependency rules.
After this feature the client can build and run the image and get a version banner — nothing more, but
every subsequent feature has a home and a test harness to land in.

## Starting state

Nothing exists in code. All naming comes from [glossary.md](../architecture/glossary.md); the package
layout to create is fixed in [overview.md §3](../architecture/overview.md).

## Scope — what to implement

1. **Package skeleton** — create the directory tree from overview §3 with `__init__.py` files and no
   logic: `tourganize/{domain/{trip,requirements,catalog,options},dialogue,ports,application,language,adapters,platform}`.
   Adapters get empty sub-packages only where a later feature fills them; do not pre-create empty
   folders for features beyond F07.
2. **Packaging** — `pyproject.toml` with a PEP 621 project table, `requires-python = ">=3.11"`, a
   `tourganize` console script pointing at `tourganize.cli:main`, and **optional-dependency groups** so
   heavy stacks stay out of the base image: `dev`, `terminal`, `typeset`, `mcp`, `knowledge`, `hosted`.
   Base install must remain pure-Python.
3. **Settings** (`tourganize/platform/settings.py`) — one frozen `Settings` dataclass built by
   `Settings.from_env(mapping)`. Every key is `TOURGANIZE_*`, has a documented default, and is validated
   at construction (unknown enum values, unreadable paths and malformed integers raise
   `ConfigurationError`). Secrets are read from the environment or from a file named by
   `TOURGANIZE_SECRETS_FILE`, are held in a `SecretValue` wrapper whose `__repr__`/`__str__` redact, and
   are never logged.
4. **Errors** (`tourganize/platform/errors.py`) — the exception root: `TourganizeError`, and
   `ConfigurationError`, `PortUnavailableError`, `ContractViolationError` under it. Every later feature
   derives its exceptions from these, never from bare `Exception`.
5. **Logging** (`tourganize/platform/logging.py`) — `configure_logging(settings)` installing
   JSON-lines or human-readable output per `TOURGANIZE_LOG_FORMAT`, with a `session_id`/`turn_index`
   context filter so later features can correlate lines without threading a logger around.
6. **Clock and TelemetrySink ports** (`tourganize/ports/platform.py`) — the two smallest ports, plus
   `SystemClock`, `FrozenClock` (test fake), `JsonlTelemetrySink` and `NullTelemetrySink`.
   `TelemetryEvent` is a frozen dataclass with `kind`, `session_id`, `occurred_at` and a `fields`
   mapping — deliberately generic so F08 can define the Turn Ledger without changing the port.
7. **CLI skeleton** (`tourganize/cli.py`) — `argparse`-based, with `tourganize --version` and
   `tourganize doctor` (prints resolved Settings with secrets redacted, the selected adapters, and a
   pass/fail line per port that is expected to be wired). Sub-commands `chat`, `resume`, `export`,
   `docs`, `catalog` are registered as stubs that exit 2 with "not implemented until Fnn" so the
   surface is discoverable from day one.
8. **Composition Root stub** (`tourganize/application/composition.py`) — `build_container(settings)`
   returning a `Container` dataclass of port slots, all `None` for now. This is the *only* place later
   features may construct adapters; the CI contract in step 10 enforces that.
9. **Test scaffolding** — `tests/{unit,integration,contracts,conversations}/`, `conftest.py` exposing
   `settings_factory` and `frozen_clock` fixtures, and the convention (documented in
   `tests/README.md`): every port gets a fake in `tourganize/adapters/<area>/fake/`, shipped by the
   feature that introduces the port, and every fake is exercised by a contract test.
10. **Quality gates** — `ruff` (lint + format), `mypy --strict` over `tourganize/`, `pytest` with
    coverage, and **`import-linter`** contracts that are the real architectural enforcement:
    - `tourganize.domain` and `tourganize.dialogue` may import only stdlib and each other;
    - `tourganize.ports` may not import `tourganize.adapters`;
    - `tourganize.adapters.*` sub-packages may not import each other;
    - only `tourganize.application.composition` and `tourganize.cli` may import `tourganize.adapters`.
11. **Containers** — `docker/app.Dockerfile` (slim Python base, non-root user, base + `terminal` extras)
    and `docker/compose.yaml` with a **`dev-cpu` profile** mounting the repo and a named volume for
    `TOURGANIZE_DATA_DIR`. No GPU, no CUDA, nothing that requires the NVIDIA runtime.
12. **CI** — one workflow running lint, type-check, import-linter and tests on push, plus a job that
    builds the app image and runs `tourganize doctor` inside it.

## Contract (the Lego connectors)

**Inputs:** process environment (and optionally a secrets file); CLI argv.

**Outputs:** an installed package, a `Container` of empty port slots, configured logging, and CLI exit
codes (`0` success, `2` unimplemented sub-command, `3` `ConfigurationError`).

```python
@dataclass(frozen=True)
class Settings:
    env: Literal["dev", "test", "prod"]              # TOURGANIZE_ENV
    log_level: str                                    # TOURGANIZE_LOG_LEVEL
    log_format: Literal["json", "human"]              # TOURGANIZE_LOG_FORMAT
    config_dir: Path                                  # TOURGANIZE_CONFIG_DIR
    data_dir: Path                                    # TOURGANIZE_DATA_DIR
    telemetry_sink: Literal["null", "jsonl"]          # TOURGANIZE_TELEMETRY_SINK
    telemetry_path: Path | None                       # TOURGANIZE_TELEMETRY_PATH
    # later features append fields here; they never re-invent loading

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> "Settings": ...

@dataclass(frozen=True)
class TelemetryEvent:
    kind: str
    session_id: str | None
    occurred_at: datetime
    fields: Mapping[str, object]
```

**Ports consumed:** none.

**Ports provided:** `Clock`, `TelemetrySink` (with `SystemClock`/`FrozenClock`,
`JsonlTelemetrySink`/`NullTelemetrySink`).

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_ENV` | Runtime profile | `dev` |
| `TOURGANIZE_LOG_LEVEL` | Python log level | `INFO` |
| `TOURGANIZE_LOG_FORMAT` | `json` or `human` | `human` in dev, `json` otherwise |
| `TOURGANIZE_CONFIG_DIR` | Root of `catalog/`, `prompts/`, `messages/` | `./config` |
| `TOURGANIZE_DATA_DIR` | Writable state (sessions, exports, indexes) | `./var` |
| `TOURGANIZE_SECRETS_FILE` | Optional `KEY=value` file merged under the environment | unset |
| `TOURGANIZE_TELEMETRY_SINK` | `null` or `jsonl` | `jsonl` |
| `TOURGANIZE_TELEMETRY_PATH` | Where the JSONL sink writes | `${TOURGANIZE_DATA_DIR}/telemetry.jsonl` |

**Errors/failure modes:** `ConfigurationError` on any invalid or unreadable setting, raised at start-up
before any port is built (fail fast, never half-configured). An unwritable `TOURGANIZE_DATA_DIR` fails
`doctor` rather than at first write. A failing `TelemetrySink` logs once at WARNING and degrades to
no-op — telemetry must never break a session.

## Out of scope

No domain types (F02). No dialogue logic (F05). No adapters of any kind beyond the two platform fakes.
No GPU image or NVIDIA runtime (F20). No dependency pinning exercise — declare ranges, let the lock
file be generated. No web framework anywhere in this feature.

## Replaceability notes

**Must be preserved:** the `TOURGANIZE_*` naming convention and `Settings.from_env`; the exception
hierarchy root; `build_container(settings) -> Container` as the single wiring point; the `Clock` and
`TelemetrySink` protocols; the test directory layout and the fake-per-port convention.

**Free to change:** argparse vs. another CLI library; JSON logging implementation; the container base
image; whether Settings is a dataclass or a pydantic model (as long as `from_env` and immutability
hold); CI provider.

## Definition of done

- [ ] `pip install -e ".[dev]"` succeeds on Python 3.11 and 3.12.
- [ ] `tourganize --version` prints the package version; `tourganize chat` exits 2 with a message naming
      the feature that will implement it.
- [ ] `tourganize doctor` prints resolved Settings with **no secret value visible** in the output, and a
      unit test asserts that a `SecretValue` never appears in `repr`, `str`, or log output.
- [ ] `Settings.from_env({})` yields all documented defaults; a test asserts each default, and invalid
      values for every enum/int/path key raise `ConfigurationError`.
- [ ] `ruff check`, `ruff format --check`, `mypy --strict tourganize`, and `pytest` all pass locally and
      in CI.
- [ ] `lint-imports` passes, and a **deliberately violating test fixture** (a module importing an
      adapter from `tourganize.domain`) is shown to fail it — the enforcement is proven, not assumed.
- [ ] `docker compose --profile dev-cpu build` succeeds and
      `docker compose --profile dev-cpu run --rm app tourganize doctor` exits 0 as a non-root user.
- [ ] `JsonlTelemetrySink` writes one parseable JSON object per event to `TOURGANIZE_TELEMETRY_PATH`; a
      test asserts the file contents and that a write failure degrades to a warning without raising.
- [ ] `tests/README.md` documents the fake-per-port and contract-test convention.
- [ ] `docs/architecture/overview.md` §3 matches the created tree (fix the doc if the tree diverges).

## Open questions / risks

- **Implementer's call:** argparse vs. Typer/Click; `dataclass` vs. pydantic for Settings; exact base
  image tag; whether the JSONL sink buffers.
- **Risk:** over-building the CLI here. The sub-commands are stubs on purpose; if this feature starts
  growing behaviour, it is doing another feature's work.
- **Risk:** the import-linter contracts are the single most valuable artefact in this feature. If they
  are weakened to make a later feature compile, the DDD constraint (C2) quietly dies. Weakening one
  requires an ADR entry.
