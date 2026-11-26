# Changelog

## Unreleased (Phase 0: results as IR)

### Added
- **Lowered transactor inputs** (fw-hdl xtor-shell-plan T1/T2/T4): `SyncXingRecord` (a lowered crossing: path, direction, item type, depth, port name per role), in the new `DataTypeComponent.sync_xings`; `ProcBlock.args` (a task block's formal arguments), and a task block's `body` now holds its code; `DataTypeComponent.task_blocks`, the task blocks the crossing lowering keeps apart from the RTL blocks; `ApiEndpoint.api_params` (a parameterized API's specialization) and `ApiEndpoint.api_scope` (the scope that declares the API), for the bridge (T4). All of it round-trips through the serializer.
- **`Arg.direction`** (fw-hdl protocol-kit K5.2c): an argument's direction, a `SignalDirection`, or None.
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
- **Protocol IR** (`protocol_hub.py`, fw-hdl protocol-kit-plan §8.11): an API is the existing `DataTypeProtocol`. `DataTypeProtocolHub` (a protocol family, stored once in `type_m`: template params and `apis` by `ProtoDir`), `ProtocolRef` (a specialization: family and named params), `ProtocolApi`, `ProtocolConverter` (transactor or abstractor), `BundleProtocol`, `ApiEndpoint`, `ApiConnection`, `CfgPublisher`, `CfgConsumer` and `CfgConnection`. `DataTypeClass` gains `implements` and `bundle_protocol`; `DataTypeComponent` gains `converter`, `bundle_protocols`, `api_endpoints`, `api_connections`, `cfg_publishers`, `cfg_consumers` and `cfg_connections`. All of it round-trips through the serializer.
  - Later (fw-hdl protocol-kit-followups F3): `DataTypeProtocolHub` loses `cfg_type`, `functions`, `xfer_time` and `mismatch`. The spec puts configuration on endpoints and timing on channels, and nothing set them. `DataTypeBundle` gains `bundle_protocol` (fw-hdl-fe-sv writes bundle classes as `DataTypeBundle`).
  - Later (F4): `CfgListener` (a listener registered on a configuration sink) in the new `DataTypeComponent.cfg_listeners`. The IR connects a change to the sinks and listeners it reaches; it does not carry values.
- **Process-block IR** (`proc_block.py`, fw-hdl core-layer2 §7): `ProcBlock` (a class-written component's `step` or `comb` block, `ProcKind`: its function and body, the `clock` and `reset` `DomainRef`s of a step block, its `reset_body` from `reset_state()`, and the paths it `writes`) in the new `DataTypeComponent.proc_blocks`; `ExprInReset`, a reset domain's assertion in a lowered body. Round-trips through the serializer.
  - Later (fw-hdl sync-task-domains X1): `ProcKind.TASK`, a component's task code (`run()`), never lowered; `ProcBlock.reads`, the signals a block reads; `XingPort`, a crossing port (fw-hdl-spec `processes.md`, Crossings) that a pass derives from a component's blocks, with the step block that gives its default. Round-trips through the serializer.
- **Packages** (`package.py`, fw-hdl protocol-kit-followups §18, Q19): `Package` (its `items` in source order: `PackageImport`, `PackageConst`, package-scope `Function` and `PackageType`, a type named by its qualified name and defined in `type_m`), with `imports`/`constants`/`functions`/`types` accessors; `Context.packages`. `ExprRefPackage` refers to a package member (`pkg::name`). All of it round-trips through the serializer.
- **Class-scope types** (§18, Q20): `TypeDef` (a local name for a type), in `DataTypeClass.typedefs` and `DataTypeProtocol.typedefs` (source order, written over a template's parameters), and `DataTypeMemberRef` (`member` of `base`: SV `axi4_types #(P)::aw_t`, `t::aw_t`).
- **`DataTypeParameterized.body`** (fw-hdl protocol-kit-plan K5.2): a template's body (an SV parameterized class), written over its parameters.
- **JSON Schemas** for all four formats (`schemas/`, `docs/formats.md`), and `jsonschema` as a `test` extra.

### Changed
- `BlockSignature` format v2: channels gain `stage` and `flop_kind`; struct and array payloads are supported. v1 files are still read.
