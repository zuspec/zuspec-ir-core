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
- **Domain IR** (`domain.py`, fw-hdl core-domains-plan §6.2): `DataTypeDomainKind` (a kind, stored once in `type_m`; clock and reset are instances), `DataTypeDomain`, `DomainRef`, `DomainDecl`, `DomainAssoc`, `DomainDriver`, `DomainWaiver`, and the enums `DomainRel` and `DomainSeverity`. `DataTypeComponent` gains `domains`, `domain_assocs`, `domain_drivers` and `domain_waivers`; `ExternPin` gains `domain`, `domain_kind`, `domain_attr` and `output`. All of it round-trips through the serializer. `clock_domain`/`reset_domain` are unchanged.
  - Later (D5): `DomainPortName` and `DomainProvides`, in the new `DataTypeComponent.domain_port_names` and `domain_provides`; `DataTypeDomainKind.pin_default`, and `port_names` keyed by `port_name_key(assoc, attr)`.
  - Later (D6): `DataTypeDomainKind.polarity`, the pin attribute that is a net's polarity, for the inverted nets of emission.
  - Later (D8): `DataTypeComponent.clock_domain`/`reset_domain` (a front end's own domain objects) are no longer serialized; the domain IR is what a serialized component has. `Serializer` and `JsonConverter` skip a field whose metadata has `serialize: False`.
- **JSON Schemas** for all four formats (`schemas/`, `docs/formats.md`), and `jsonschema` as a `test` extra.

### Changed
- `BlockSignature` format v2: channels gain `stage` and `flop_kind`; struct and array payloads are supported. v1 files are still read.
