# File formats

Zuspec writes four JSON formats. Each file carries `format` and `version` fields, and each has a JSON Schema in the package at `zuspec/ir/core/schemas/`. `zuspec.ir.core.schemas.schema(fmt)` returns the schema for a format. `tests/test_schemas.py` checks that every file the code writes validates.

| `format` | Version | Schema | Written by | Spec |
|---|---|---|---|---|
| `zuspec.anchor-table` | 2 | `anchor-table.v2.json` | `AnchorTable.save` | [anchors.md](anchors.md) |
| `zuspec.overlay` | 1 | `overlay.v1.json` | `Overlay.save` | [results.md](results.md) |
| `zuspec.ir-bundle` | 1 | `ir-bundle.v1.json` (the manifest) | `write_bundle` | [results.md](results.md) |
| `zuspec.block-signature` | 3 (reads 1–3) | `block-signature.v3.json` | `save_signature` | `signature.py` |

## Anchor table (`anchors.json`)

- **`scope_hashes`:** maps each named scope to 16 hex digits.
- **`anchors`:** maps each anchor to an entry with these fields:
  - `kind`: one of `type`, `member`, `stmt`, `expr`
  - `scope`
  - `loc` (optional): `[file, line, column]`, absolute path, 1-based
  - `source_name` (optional)
- **Version 2:** the grammar gained named locals (`v:<name>`, `v:<name>+K`) and synthesized nodes (`x:<reason>`). Version 1 tables are refused. Their anchors mean something else, so an overlay on them would attach to the wrong nodes. Rebuild instead.

## Overlay (`overlays/<engine>-<id>.json`)

- **`header`:** the engine and its version, the constraints and their hash, the command, the creation time, the scope hashes the engine saw, coverage, the engine's artifacts, and `overlay_id`.
- **`facts`:** one column per fact kind (`ns.name`). A column holds three parallel arrays: `anchor`, `value` and `quality`.
  - `quality` is one of `exact`, `merged`, `derived`, `partial`, `unmapped`.
  - The schema checks the structure only. Each value is checked against its kind by `results.kinds.validate`, which runs on load.

## IR bundle (`<name>.zir/manifest.json`)

The manifest names the bundle's files:
- `anchors`: the table's path
- `overlays`: the overlay files' paths
- `model`: a serialized model (`model.yaml`), or `null`

A bundle holds everything a report, viewer or diff needs to place an engine's results in source terms, without the engine's own files.

## Block signature (`*.sig.json`, `iface.block` facts)

A block's ports and channels as integration sees them. Version 2 added two fields to each channel:
- `stage`: the pipeline stage of the block's operation on the channel
- `flop_kind`: one of `none`, `flop`, `skid`, `zero_latency`

Version 3 (Phase 1, pin parity) describes function blocks too:
- `kind`: `proc` (talks through channels) or `function` (an XLS function block: argument and result ports with no handshake of their own)
- `data_ports`: a function block's ports, each with `name`, `direction`, `type` (a payload type) and `sv_type`
- `valid_ports`: a function block's valid pins by role, `input_valid` and `output_valid`, when it is pipelined

It is a version bump rather than an added field because a version-2 reader would see a function block as having no pins at all. Version 1 and 2 files are still read, as procs.

## Versioning policy

- **Bump `version`** for any change that alters what an existing file means. Examples: an anchor-grammar change, or renaming a field.
- **Additive optional fields** don't need a bump.
- **Readers** list the versions they accept and refuse the rest with a message naming both the file's version and the versions they know.
- **Schemas** are named `<format>.v<N>.json`. A new version adds a file and keeps the old ones.
