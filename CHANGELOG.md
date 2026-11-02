# Changelog

## Unreleased (Phase 0: results as IR)

### Added
- **Anchors** (`anchor.py`, `docs/anchors.md`): stable, name-first identity for every type, member, statement and expression.
  - `assign_anchors` and `AnchorTable` (format `zuspec.anchor-table` v2), with scope hashes.
  - Named locals: `v:<name>`, `v:<name>+K`.
  - Synthesized nodes: `x:<reason>`, marked with `mark_synthesized`.
- **`Base.anchor`:** inert, and ignored by equality.
- **`Loc.key()`:** 1-based, absolute paths.
- **Results** (`results/`, `docs/results.md`):
  - `Fact` with the qualities `exact`, `merged`, `derived`, `partial` and `unmapped`
  - core fact kinds `iface.*`, `sched.*`, `timing.*` and `rtl.*`, plus registration through the `zuspec.fact_kinds` entry point
  - columnar `Overlay` (`zuspec.overlay` v1)
  - `ResultStore`, which reports each fact as valid, stale or orphan
  - IR bundles (`zuspec.ir-bundle` v1)
- **Engine interface** (`engine.py`, `docs/engine.md`): `Engine`, `Constraints`, `BuildResult` and `Verdict`, and a registry under the `zuspec.engines` entry point.
- **JSON Schemas** for all four formats (`schemas/`, `docs/formats.md`), and `jsonschema` as a `test` extra.

### Changed
- `BlockSignature` format v2: channels gain `stage` and `flop_kind`; struct and array payloads are supported. v1 files are still read.
