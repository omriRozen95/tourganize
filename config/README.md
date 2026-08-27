# `config/` — the data half of the application

Everything in here is **configuration read at run time**, not code. It is the mechanism
behind the rule that adding a travel topic requires no Python change: a Component Kind is a
YAML entry, a prompt is a versioned file, a phrasing is a message key.

`TOURGANIZE_CONFIG_DIR` points at this directory (default `./config`).

| Path | What lands here | Feature |
|---|---|---|
| `catalog/components.yaml` ✔ | Component Kinds: `kind_key`, priority weight, outcome dependencies, requirement schema key | F02, F03, F04 |
| `catalog/schemas/<schema_key>.yaml` ✔ | Requirement Schemas: the fields that describe a traveller's wish, their obligations, and the blocking rules | F03 |
| `prompts/<version>/` | Prompt Templates, with their declared variables and expected output schema | F08 |
| `messages/<locale>.yaml` | The Message Catalogue: `en.yaml`, `he.yaml` | F10 |

`TOURGANIZE_CATALOG_PATH` overrides the catalog's location alone, and defaults to
`${TOURGANIZE_CONFIG_DIR}/catalog/components.yaml`. `TOURGANIZE_SCHEMA_DIR` does the same for
the Requirement Schemas, and defaults to `${TOURGANIZE_CONFIG_DIR}/catalog/schemas`.

A Component Kind's `schema_key` is the *file name* of its Requirement Schema: `lodging.v1`
resolves to `catalog/schemas/lodging.v1.yaml`, and that file must declare the same
`schema_key` and the `component_kind` that named it. Both halves are checked at load, because
a kind and its schema are one declaration split across two files.

The Component Catalog is the file F02 filled in, and it is the **only** place in the
repository where a travel topic exists: an automated test asserts that grepping
`tourganize/` for a shipped `kind_key` returns nothing. Adding a topic is an entry here.

These files are read by a deliberately small YAML reader
(`tourganize/platform/yaml_subset.py`, see D13), which refuses anything outside the subset
it documents rather than guessing. `tourganize catalog validate` loads the catalog **and
every enabled kind's Requirement Schema**, and reports every problem it finds; `tourganize
doctor` reports the catalog as one check. `tourganize catalog gaps --kind <k>
[--set '<json>']` prints what a component still needs — the closest thing to running the
dialogue before the dialogue exists.
