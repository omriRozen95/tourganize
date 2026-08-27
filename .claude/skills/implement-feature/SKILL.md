---
name: implement-feature
description: "Implement one numbered feature file from docs/features/ (F07, F08, …) end to end: branch, minimal read set, fan-out along the architecture's seams, the parallel gate run, the Definition-of-done proof and the commit. Use whenever the user asks to build, implement or land a feature by its F-number, or says 'the next feature'."
---

One feature, one branch, one commit. The feature file is the specification; its **Definition of
done** is the acceptance criteria. `CLAUDE.md` is the standing law and is already in context —
this skill is only the procedure.

## 1. Pin the feature

If the user named an F-number, that is it. Otherwise the next one is the first row of the ordered
table in `docs/roadmap.md` whose feature is not yet implemented — `CLAUDE.md` names it under *"The
next thing to build"*.

```bash
n=07   # the feature number, two digits
file=$(ls docs/features/F$n-*.md) && echo "$file"
git switch -c "feat/$(basename "$file" .md | tr 'A-Z' 'a-z')"
```

Branch names are the feature file's stem, lower-cased, under `feat/`: `feat/f07-presentation-surface-and-terminal-shell`.

## 2. Read the minimum, grep the rest

A feature file is written to be self-sufficient. Read in full:

- the feature file itself;
- the **Contract** section — and only that section — of each file named in its `**Depends on:**`.

Do **not** read `glossary.md`, `decisions.md` or `overview.md` end to end. They are ~14k words and
you need a handful of entries. Pull those instead:

```bash
# every term the feature file names, looked up where it is defined
grep -n -A6 -iE "^#+ .*(term one|term two)" docs/architecture/glossary.md
grep -n -A8 -E "^#+ *D1?[0-9]" docs/architecture/decisions.md | grep -iB2 -A8 "<topic>"
grep -rn "<PortName>" docs/architecture/overview.md
```

Read a whole architecture doc only when the feature *changes* one.

Then read the code you are extending, not the code near it: the ports the feature consumes, the one
composition root (`tourganize/application/composition.py`), and the contract suite of any port you
are adding an adapter to.

## 3. Lay the seams before fanning out

Write, in one pass and alone, the things everything else compiles against:

- the port protocol(s) in `tourganize/ports/` (or `tourganize/dialogue/ports.py` where D17 applies);
- the domain/dialogue value objects the contracts are typed with;
- the new `Settings` fields and `TOURGANIZE_*` keys with their defaults;
- the error types, named as the glossary names them.

Signatures and docstrings, `...` bodies. Run `scripts/check` — mypy will accept it — and only then
split the work.

**Then fan out along the architecture's own seams**, one agent per seam, in a single message so they
run concurrently. F06's split is the model:

| Agent | Owns |
|---|---|
| domain / port | the pure types and the protocol, if not already laid |
| adapter | the concrete implementation behind the port |
| data | fixtures, YAML, phrase tables — mechanical, hand this one to a cheap model |
| service | the application-layer orchestration over the port |
| tests | the contract suite parametrised over every adapter, including the fakes |
| wiring | composition root, CLI command, README/CLAUDE.md/glossary edits |

Give each agent the seam's signatures verbatim, the invariants from §5 that touch it, and the rule
that it may not edit another agent's files. Solo the whole thing when the feature is S-sized.

## 4. Check with one command

```bash
scripts/check                       # ruff, ruff format, mypy --strict, lint-imports, pytest — in parallel
scripts/check tests/unit/test_x.py  # pytest narrowed; the other three gates still run
scripts/check --cov                 # the coverage report CI prints
```

Never run the four gates one at a time: it is four round-trips to learn what one gives you, and it
stops at the first failure. Coverage is not in `addopts` — do not put it back.

## 5. The invariant checklist

Before claiming done, walk `CLAUDE.md` § *Invariants that must not be broken* against the diff. The
ones features break most often:

- **The domain imports nothing.** If `lint-imports` has to be weakened to compile, that is an ADR in
  `docs/architecture/decisions.md`, not a config edit.
- **No topic strings in `tourganize/`.** Flights, lodging and dining are configuration.
- **No prose in the domain.** Acts carry message keys and structured data.
- **Everything external enters through a port**, with at least one fake, selected only in
  `tourganize/application/composition.py`.
- **Determinism**: the same query yields a byte-identical result, in any process.
- **Degrade, never die**: one source, adapter or provider failing is a diagnostic, not an exception.
- A guard test that starts failing means the code is wrong. Fix the code.

New vocabulary goes into `docs/architecture/glossary.md` **in the same change**. New env keys go
into the README table and `Settings.from_env` together.

## 6. Prove the Definition of done

Every DoD item is observable by running something. Run them all and keep the output — that output is
what you report, not a claim that it passed. If an item cannot be observed, say so explicitly rather
than marking it done.

If you edited anything under `docs/`, re-run both spec-integrity checks from `CLAUDE.md`
(dependency-edge parity and link resolution).

## 7. Commit

One commit, subject `Implement F07: presentation surface and terminal shell` — the feature title,
lower-cased. The body is prose: what was built, every decision that a reader would otherwise have to
reverse-engineer, and any deviation from the feature file with the reason. Follow-ups after review
are `Address the F07 code review: <the one-line theme>`.

Commit or push only when asked.
