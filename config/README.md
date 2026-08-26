# `config/` — the data half of the application

Everything in here is **configuration read at run time**, not code. It is the mechanism
behind the rule that adding a travel topic requires no Python change: a Component Kind is a
YAML entry, a prompt is a versioned file, a phrasing is a message key.

`TOURGANIZE_CONFIG_DIR` points at this directory (default `./config`).

| Path | What lands here | Feature |
|---|---|---|
| `catalog/components.yaml` ✔ | Component Kinds: `kind_key`, priority weight, outcome dependencies, requirement schema key | F02, F03, F04 |
| `prompts/<version>/` | Prompt Templates, with their declared variables and expected output schema | F08 |
| `messages/<locale>.yaml` | The Message Catalogue: `en.yaml`, `he.yaml` | F10 |

`TOURGANIZE_CATALOG_PATH` overrides the catalog's location alone, and defaults to
`${TOURGANIZE_CONFIG_DIR}/catalog/components.yaml`.

The Component Catalog is the file F02 filled in, and it is the **only** place in the
repository where a travel topic exists: an automated test asserts that grepping
`tourganize/` for a shipped `kind_key` returns nothing. Adding a topic is an entry here.

These files are read by a deliberately small YAML reader
(`tourganize/platform/yaml_subset.py`, see D13), which refuses anything outside the subset
it documents rather than guessing. `tourganize catalog validate` loads the catalog and
reports every problem it finds; `tourganize doctor` reports the same thing as one check.
