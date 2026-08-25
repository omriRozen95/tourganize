# `config/` — the data half of the application

Everything in here is **configuration read at run time**, not code. It is the mechanism
behind the rule that adding a travel topic requires no Python change: a Component Kind is a
YAML entry, a prompt is a versioned file, a phrasing is a message key.

`TOURGANIZE_CONFIG_DIR` points at this directory (default `./config`).

| Path | What lands here | Feature |
|---|---|---|
| `catalog/components.yaml` | Component Kinds: `kind_key`, priority weight, outcome dependencies, requirement schema key | F02, F03, F04 |
| `prompts/<version>/` | Prompt Templates, with their declared variables and expected output schema | F08 |
| `messages/<locale>.yaml` | The Message Catalogue: `en.yaml`, `he.yaml` | F10 |

F01 creates the directory and nothing else — `tourganize doctor` reports it as "does not
exist yet" until the first feature that needs it fills it in.
