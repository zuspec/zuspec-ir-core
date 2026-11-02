# Anchors: stable identity for IR nodes

*Status: **accepted** 2026-11-01 (Phase 0 item A1); amended 2026-11-01 with named locals and synthesized nodes (P1, P2: §2.3, §2.6; evidence in fw-hdl `docs/research/anchor-adr-proposals.md`). Implemented in `zuspec/ir/core/anchor.py`; anchor-table format version 2.*

An **anchor** is a stable, serializable name for an IR node. Engine results (schedule,
timing, RTL names, area) are recorded as *facts* keyed by anchor, in *overlays* held beside
the model (see `results.md`). Report renderers, editor tooling and diff tools find their way
back to user source through anchors alone. Anchors therefore have to be:

- **Language-neutral:** any front end produces them the same way, because one pass
  (`assign_anchors`) assigns them from the IR, not from source text.
- **Deterministic:** the same IR always yields the same anchors.
- **Stable under unrelated edits:** an edit re-keys only the scope it touches.
- **Inert:** a string, never an object reference. Setting an anchor never changes whether
  two nodes compare equal.

## 1. Grammar

```
anchor     := root ( "." member )? ( "#" stmtpath )? ( "." expr )? qualifier?
root       := segment ( "." segment )*        neutral qualified type name, e.g. rle_pkg.RunLengthEncoder
member     := segment                         a named member of the type (see §2.2)
stmtpath   := stmt ( "." blockpath "." stmt )*
stmt       := "s" N                           N: 1-based position, before any declaration in its list
            | "v:" segment ( "+" N )?         a declaration of a local / the N-th statement after it (§2.3)
            | "x:" segment                    a synthesized statement (§2.6)
blockpath  := field ( "." N "." field )?      the IR field holding the nested list, e.g.
                                              body | orelse | clauses.2.body | cases.3.body
expr       := "e" N ( ".x:" segment )?         N: pre-order position among user expressions (§2.4);
                                              the suffix names a synthesized wrapper of it (§2.6)
            | "x:" segment                    a synthesized expression with no user expression under it
qualifier  := "@" word ( ":" word )*          derived anchors only (§3)
segment    := name, with reserved characters escaped (§4)
```

### Examples

| Anchor | Node |
|---|---|
| `rle_pkg.RunLengthEncoder` | the component type |
| `rle_pkg.RunLengthEncoder.prev_count` | a field |
| `rle_pkg.RunLengthEncoder.run` | a process (a `Function` in `proc_processes`) |
| `rle_pkg.RunLengthEncoder.run#s4` | 4th statement of `run`'s body (no declaration before it) |
| `rle_pkg.RunLengthEncoder.run#s1.body.v:symbol` | the declaration of local `symbol` in the body of `run`'s 1st statement |
| `rle_pkg.RunLengthEncoder.run#s1.body.v:last+3` | the 3rd statement after the declaration of `last` |
| `gcd_pkg.gcd#x:retvar` | the return variable the SV front end declares (synthesized) |
| `gcd_pkg.gcd#v:i+1.e3.x:cast` | an implicit cast the front end put around expression `e3` |
| `rle_pkg.RunLengthEncoder.run#s4.body.s1` | 1st statement of the `body` of that statement (an `if`) |
| `rle_pkg.RunLengthEncoder.run#s4.orelse.s2.e3` | 3rd expression of the 2nd `else` statement |
| `gcd_pkg.gcd#s3.cases.2.body.s1` | 1st statement in the 2nd case of a match, in function `gcd` |
| `rle_pkg.RunLengthEncoder.prev_count.e1` | the field's `initial_value` expression |
| `rle_pkg.RunLengthEncoder.prev_count@reg:2` | derived: the pipeline register holding `prev_count` at the end of stage 2 |

## 2. Assignment rules

### 2.1 Roots
- Each entry of `Context.type_m` that `assign_anchors` is asked to cover becomes a root.
- **The root is the neutral qualified name:**
  - the package and type names, joined by `.`
  - the front end's own spelling (SV `rle_pkg::RunLengthEncoder`, Python
    `mod.RunLengthEncoder`) is kept as the anchor table's `source_name`
- **Free functions** (a package-scope SV function, a module-level Python function) are
  roots in their own right: `gcd_pkg.gcd`.

### 2.2 Members
- **What counts as a member:** every `Base` node with a `name` attribute that sits directly
  in a list-valued field of a root type. That covers:
  - `fields`
  - `functions`
  - `sync_processes`, `comb_processes`, `wire_processes`, `proc_processes`
  - `methods`
  - and any list field added later
- **Collisions:** if two members share a name (which no current front end produces), the
  second and later get `~2`, `~3`, … in IR order. Front ends should reject the case
  instead.
- **Members do not nest.** A field's struct type is anchored at its own root.

### 2.3 Statements
- A **statement list** is any dataclass field of type `List[Stmt]`:
  - the `body` of a `Function`/`Process`
  - an action's `body_stmts`
  - `body`/`orelse`/`finalbody` of compound statements
- **Positions:** statements are numbered 1, 2, … within their list, up to the first
  declaration.
- **Named locals (P2):** a statement that declares a local (`StmtAnnAssign` whose target
  has a name) is `v:<name>`. Each later statement is `v:<name>+K`, the K-th since that
  declaration, until the next declaration. A name declared twice in one list is
  `v:<name>~2`. An insertion therefore re-keys only the statements up to the next
  declaration, not the rest of the list.
- **Owner-level lists:** a root's or member's `body` list is written `#<seg>`. Any other
  statement list it holds (an action's `body_stmts`, say) is written `#<field>.<seg>`.
- **Generic `metadata` dicts are never walked.**
- **Nested lists** are named by their field path from the enclosing statement:
  - a list held directly by the statement: its field name (`body`, `orelse`)
  - a list inside an element of a list field: `field.N.field`, for example `clauses.2.body`
    (2nd `StmtIfClause`) or `cases.3.body` (3rd `StmtMatchCase`)
  - this covers every current and future compound statement, with no per-kind table
- **Inserting or deleting a statement** re-keys only the later siblings in the same list,
  up to the next declaration, and their subtrees.

### 2.4 Expressions
- **Ownership:** each `Expr` node belongs to the nearest enclosing statement, or to a
  member when it is not inside any statement (`initial_value`, `width_expr`,
  `sensitivity_list`).
- **Numbering:** within its owner, expressions are numbered in **pre-order**:
  - a parent before its operands
  - operands in dataclass field order, and list order within a field
- **Nested statements own their own expressions.** Numbering does not descend into a
  nested statement list. So an `if`'s condition is `#s4.e1`, and expressions in its body
  are under `#s4.body.s…`.
- **Every user `Expr` is numbered, not only "value-producing" ones.** That keeps the rule
  mechanical; consumers ignore anchors they have no facts for. Synthesized expressions are
  not numbered (§2.6).
- **Type annotations are not expressions here.** A field named `annotation` (a local's
  `x: u8`, an argument's annotation) is part of the declaration and gets no anchors, so a
  front end that spells types as expressions numbers the same as one that does not.

### 2.5 What is not anchored (Phase 0)
- **Type annotations** (§2.4).
- **`DataType` nodes other than roots:** an inline struct type, for example. Its uses are
  anchored where they appear.
- **Annotation, provenance, and doc payloads.**
- **Nodes in the generic `metadata` dicts.**

### 2.6 Synthesized nodes (P1)
A front end marks a node the user did not write with
`mark_synthesized(node, reason)`: `provenance.pass_name == "synth:<reason>"`. A `pass`
statement is a placeholder and always counts as synthesized. Synthesized nodes take no
ordinal, so whether a front end needs them does not shift the anchors of what the user
wrote, and two front ends agree on those:

- a synthesized **statement** is `x:<reason>` in its list (`~N` when repeated); its own
  expressions and nested statements are under it as usual;
- a synthesized **expression** is anchored on the first user expression under it,
  `…eK.x:<reason>` (an implicit cast around `eK`); with none under it, on its owner,
  `…x:<reason>`.

Reasons the fw-hdl SV front end uses: `retvar`, `return` (falling off the end),
`cast` (implicit conversion), `literal-type` (the cast giving a literal its type),
`index` (array offset arithmetic), `incr` (the `1` of `++`), `forever`. zuspec-dataclasses:
`cast`. A front end that marks nothing still works; it is only less neutral.

Measured on the canary GCD (SV versus zuspec-dataclasses): user anchors 40 and 37, 36
shared; the remainder is a real source difference. Before P1/P2: 59 and 46 anchors,
14 shared.

## 3. Derived anchors

Engines create things with no IR counterpart. They are named by an anchor plus a
**qualifier**:
- the anchor is the IR node the thing belongs to
- the qualifier is `@kind[:arg]*`

The Phase 0 vocabulary:

| Qualifier | Meaning |
|---|---|
| `@reg:<stage>` | pipeline register holding the value at the end of `<stage>` (XLS `p<stage>_…`) |
| `@fifo` | FIFO instance implementing a channel |
| `@flow:<stage>` | flow-control logic of a stage (on a process or function anchor) |
| `@state` | state register (on a field or local anchor) |

New qualifiers are namespaced by engine (`@xls.<kind>`) until a second engine needs the
same thing, at which point they move into this table.

## 4. Escaping

- **Reserved characters:** `.` `#` `@` `:` `~` `+` `\` and whitespace.
- **Escaping:** a reserved character in a name is written `\` plus two lowercase hex
  digits (`a.b` → `a\2eb`).
- **Encoding:** anchors are ASCII, and non-ASCII characters are escaped as UTF-8 bytes the
  same way.
- **Round trip:** `parse(format(x)) == x` for all names.

## 5. Scope hashes

- **Named scopes:** roots and members. Each gets a **content hash**: the first 16 hex
  digits of a SHA-256 over the canonical serialization of its subtree.
- **What the hash ignores:** `loc`, `doc`, `doc_trailing`, `anchor`, `annotations` and
  `provenance`. So moving code without changing it, or editing comments, leaves the hash
  unchanged.
- **Use:** overlays record the hashes they were built against. On a rebuild, a fact whose
  scope hash still matches is `valid`; otherwise it is `stale`. If its anchor no longer
  exists, it is `orphan`.
- **Stale facts are kept, not discarded.** An editor can show the last build's schedule,
  greyed out, while the user edits.

## 6. Source locations (`Loc`)

Anchors give identity; `Loc` gives position. The anchor table records the `Loc` of every
anchored node, and the `(file, line, column)` index is how engine adapters map an engine's
source positions back to anchors.

**Conventions:**
- **`Loc.line` is 1-based.**
- **`Loc.pos` is a 1-based column.**
- **`Loc.file` is the path as given.** `Loc.key()` normalizes it to an absolute path.

**Front-end obligations:**
1. Set `loc` on declarations (types, members), statements and expressions.
2. Use 1-based lines and columns.
3. Run `assign_anchors(ctx)` after building the IR, and before any pass that clones or
   rewrites nodes.
4. Mark every node the user did not write with `mark_synthesized(node, reason)` (§2.6):
   return variables, implicit casts, the type-giving cast around a literal, offset
   arithmetic. Leave casts the user wrote unmarked.
5. Declare locals with `StmtAnnAssign` whose target carries the source name (§2.3).

## 7. Open points (to settle with the edit-history measurement, Phase 0 item A6)

- **Named locals:** settled by A6 (P2, §2.3). Churn in edited scopes fell from 41% to 10%
  of matched nodes (fw-hdl `tools/anchor_churn.py`).
- **Large unnamed bodies:** whether a process body with very many statements needs a
  coarser stable unit, such as a labelled block.
