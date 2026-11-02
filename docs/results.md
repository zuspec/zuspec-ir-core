# Engine results as IR: facts, overlays and the IR bundle

*Phase 0 (items B1–B6). Implemented in `zuspec/ir/core/results/`. Anchors are specified in
[anchors.md](anchors.md).*

A synthesis engine reports schedules, delays, register names and interfaces. Zuspec keeps
all of that as **IR data bound to the model**, not as engine-specific report files. One data
model then serves three uses:
- generating new, lower-level Zuspec models from results
- reporting and visualization, mapped back to user source
- comparing runs and engines

## 1. Three kinds of data

| Kind | Example | Where it lives | Changes the model? |
|---|---|---|---|
| **Intent** | target II, clock, "at most 3 stages" | the model (`Base.annotations`, a constraints model) | yes: it is input |
| **Facts** | stage of an operation, path delay, RTL register name | an **overlay**, keyed by anchor | no |
| **Refinements** | timing shell, staged TLM | a new model whose nodes carry `Provenance` back to anchors | n/a |

Turning a fact into intent ("freeze this schedule") is an explicit step, so what the user
asked for and what an engine decided are always distinguishable.

## 2. Facts

```python
Fact(kind="sched.stage", anchor="rle_pkg.RunLengthEncoder.run#s4.e2", value=1, quality="exact")
```

- **`kind`** is `namespace.name`.
  - **Core namespaces** (`iface`, `sched`, `timing`, `rtl`, `area`, `sim`) hold
    standardized kinds with value schemas. They are validated, and an unknown core kind is
    an error.
  - **Any other namespace** (`xls.node_name`) is an engine extra: stored as is, never
    validated. A kind joins the core once a second producer fills it.
- **`anchor`** may be derived (`…prev_count@reg:2`).
- **`quality`** says how well the engine's node maps back to the anchor:

| Quality | Meaning |
|---|---|
| `exact` | one engine node ↔ one anchor |
| `merged` | one engine node ↔ several anchors (e.g. common-subexpression elimination) |
| `derived` | engine-created; recorded on an anchor plus qualifier |
| `partial` | maps only to an enclosing statement or scope |
| `unmapped` | no source counterpart found; recorded on the enclosing function or process so totals add up |

**Lossy engines are acceptable.** An engine that keeps little source position information
still produces a valid overlay; it only limits which analyses are possible. Consumers must
degrade on `partial`/`unmapped` facts, never fail.

### Core kinds (Phase 0)

| Kind | Value |
|---|---|
| `iface.block` | `BlockSignature` JSON (`zuspec.block-signature`) |
| `iface.channel_stage` | int ≥ 0 |
| `iface.channel_flop` | `none` \| `flop` \| `skid` \| `zero_latency` |
| `sched.stage` | int ≥ 0 |
| `sched.stage_count` | int ≥ 1 |
| `sched.ii` | `{value: int ≥ 1, reliable: bool}` |
| `timing.node_delay_ps`, `timing.path_delay_ps` | int ≥ 0 |
| `timing.critical_path` | `{stage, anchors: [str], delay_ps, slack_ps?}` |
| `timing.clock_period_ps` | int ≥ 1 |
| `rtl.signal` | `{module, name}` |
| `rtl.register` | `{module, name, width, stage}` |

`area.*` and `sim.*` are reserved; their kinds are defined when a producer exists.

**Registering kinds.** A package registers more kinds through the entry-point group
`zuspec.fact_kinds`, whose callable returns a list of `FactKind`, or by calling
`results.register()`.

## 3. Overlays

An `Overlay` is the facts from one run against one version of a model:
- **`OverlayHeader`** records:
  - `engine`, `engine_version`, `delay_model`
  - `constraints` and their hash
  - `command`, `created`
  - **`scope_hashes`**: the anchor table's hashes when the engine ran
  - **`coverage`**: how much of the design maps back to source
  - **`artifacts`**: the engine's own files
  - an `overlay_id`
- **Facts are stored in columns per kind**, which keeps them compact and fast to load.
- **Several overlays coexist:** XLS and native, main and a pull request, a sweep. They are
  compared anchor by anchor.

## 4. ResultStore

`ResultStore(anchor_table)` holds overlays and answers `facts(node_or_anchor, kind=None,
overlay=None)`. Each answer is a `FactView` with a status:
- **`valid`:** the fact's scope hash matches the current model.
- **`stale`:** the scope changed since the engine ran. Stale facts are kept, so an editor
  can show the last build's results greyed out.
- **`orphan`:** the anchor no longer exists.

## 5. File formats

| File | `format` | Version | Content |
|---|---|---|---|
| `anchors.json` | `zuspec.anchor-table` | 1 | `scope_hashes`; `anchors: {anchor: {kind, scope, loc?, source_name?}}` |
| `overlays/<engine>-<id>.json` | `zuspec.overlay` | 1 | `header` (§3, plus `constraints_hash`); `facts: {kind: {anchor: [...], value: [...], quality: [...]}}` |
| `manifest.json` | `zuspec.ir-bundle` | 1 | paths of `anchors`, `overlays`, and `model` (optional) |
| `*.sig.json` / `shell.json` | `zuspec.block-signature` | 2 (reads 1) | the block interface; v2 adds channel `stage`, `flop_kind`, and struct and array payloads |

**The IR bundle** is a directory with `manifest.json`, `anchors.json`, `overlays/` and
optionally `model.yaml`. `write_bundle()` adds to an existing bundle without removing
earlier overlays. `load_store()` gives a `ResultStore` over a bundle, without needing the
model or any engine.

**Versioning.** A reader rejects unknown formats and versions. Adding an optional field is
not a version change; changing the meaning of one is.
