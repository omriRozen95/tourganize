# `fixtures/` — recorded data, not code

Everything under this directory is **data the application reads at run time**, in the same sense
that `config/` is: no Python knows what is in here, and adding to it costs no code change. It is
also, per [D9](../docs/architecture/decisions.md), the **permanent** test default — not
scaffolding to be deleted once live providers exist.

| Path | What it is | Feature |
|---|---|---|
| `options/<kind_key>/*.json` ✔ | Fixture Provider data: recorded Plan Options per Component Kind | F06 |
| `cassettes/` | Recorded Tool Calls and their results | F15 |
| `conversations/*.txt` ✔ | Scripted transcripts: one traveller turn per line | F07, F11 |

`TOURGANIZE_FIXTURE_DIR` points at `options/` and defaults to `fixtures/options`.

## `options/<kind_key>/*.json`

One file, one Component Kind, any number of options. The directory name **is** the `kind_key`,
and a file that declares a different one is refused by name — the same rule that ties a
Requirement Schema's file name to its `schema_key`.

```json
{
  "kind_key": "lodging",
  "matchable": ["place", "date_range"],
  "match": {
    "place": ["Paris", "פריז"],
    "date_range": ["2026-01-01/2027-12-31"]
  },
  "options": [
    {
      "external_ref": "px-lodging-001",
      "facts": {"name": "Hôtel Saint-Germain", "review_score": 8.7, "refundable": true},
      "price": {"amount_minor": 74000, "currency": "EUR"}
    }
  ]
}
```

| Key | Meaning |
|---|---|
| `kind_key` | The Component Kind this file serves. Must equal the directory name. |
| `matchable` | The requirement fields this file may be matched on. `match` may only constrain fields listed here. |
| `match` | Field name → the values this file answers for. A field the query holds no value for never excludes the file: a traveller who has not said where they are going has not ruled anything out. |
| `options[].external_ref` | The provider's own reference. Unique within the file; it becomes the `option_id` and the Provenance reference. |
| `options[].facts` | Structured facts, **never prose**. A string of more than three words fails the `OptionSource` contract suite, because wording is composed per locale at presentation time. |
| `options[].price` | `amount_minor` (an integer, in the currency's minor units) plus an ISO 4217 `currency`. There are no bare amounts and no exchange rates anywhere. |

**How matching works.** The comparison is chosen by the *type of the traveller's value*, never by
the field's name — which is what keeps the reader free of any knowledge of travel:

- a Date Range matches when it **overlaps** one of the declared ranges;
- a date matches when it falls **inside** one;
- anything else matches when its text form equals a declared spelling, case- and
  accent-insensitively. `paris`, `Paris` and `PARIS` are one place; `פריז` is a second declared
  spelling, not a second rule.

**When nothing matches.** The Fixture Provider answers with a deterministic *synthetic* set
derived from the query and sets `synthesised` in the result's diagnostics, so a demonstration
never dead-ends. Synthetic options carry a variant number and a price and nothing else: a
provider that invented plausible hotel names would be one whose output nobody could tell apart
from a recording.

**Determinism.** The same query returns the same options in the same order, in any process, on
any machine — the Golden Conversations depend on it. The order is a stable permutation seeded by
`RequirementSet.digest()`, so a refinement that changes what was asked for visibly changes what
comes back.

## `conversations/*.txt`

A transcript the Scripted Surface replays: **one traveller turn per line**, read in order, and
then the surface closes by answering `None` — which is the same close signal a terminal sends on
`Ctrl+C`. Blank lines are dropped and a line starting with `#` is a comment, so a transcript can
say what each turn is meant to prove. Nothing else is in the file: no expected replies, no
assistant lines, no markup. What the assistant said is the *output* of replaying it, and pinning
that here as well would be recording the answer next to the question.

```bash
tourganize chat --script fixtures/conversations/paris.txt              # exits 0, no TTY needed
tourganize chat --locale he --script fixtures/conversations/paris.he.txt
```

`paris.txt` is the Phase 1 demo, written down. F11 turns these files into Golden Conversations by
storing the Assistant Acts each one produces beside it; until then the expectation lives in
`tests/integration/test_chat_end_to_end.py`, which is also where the act-name sequence is pinned.

## Adding a Component Kind's options

Make `options/<kind_key>/`, drop a JSON file in it, declare the Kind in
`config/catalog/components.yaml` and give it a Requirement Schema. That is the whole procedure —
`tests/integration/test_fourth_component_kind.py` is the proof that no Python change is involved.

```bash
tourganize options search --kind lodging \
  --set '{"place": "Paris", "date_range": "2026-10-23/2026-10-28"}'
tourganize doctor    # reports the Source Profile and the recorded data found, per Kind
```
