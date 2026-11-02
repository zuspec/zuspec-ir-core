# The synthesis-engine interface

*Phase 0 item D. Code: `zuspec/ir/core/engine.py`. The XLS adapter is zuspec-be-xls
`zuspec/be/xls/engine.py`.*

An engine turns a block of Zuspec IR into RTL and reports what it did as an **overlay** on
the IR's anchors ([results.md](results.md)). Flows, reports and checks use the interface,
so they do not need to know which engine built a block.

```python
class Engine(Protocol):
    name: str
    def version(self) -> Optional[str]: ...
    def accepts(self, roots, top) -> Verdict: ...        # ok + reasons
    def build(self, roots, top, anchors, constraints, workdir) -> BuildResult: ...
```

- **`roots`** is the IR the block needs: a component, or the top function and the
  functions it calls. **`top`** names the block.
- **`anchors`** is `assign_anchors(roots)`. The overlay's facts are on these anchors.
- **`Constraints`** is the intent: `clock_period_ps`, `pipeline_stages`, `reset`,
  `reset_active_low`, `module_name`, and **`extra`**, which passes engine-specific options
  through. An engine rejects options it does not know.
- **`BuildResult`** carries:
  - `rtl`: the files
  - `top_module`
  - `overlay`
  - `artifacts`: the engine's own files, by role
  - `log`

**Registry.** Engines register under the entry-point group `zuspec.engines` (a
zero-argument factory) or through `register_engine()`. `get_engine(name)` returns one.

**Why the interface lives in ir-core.** It sits beside the results model, so an adapter
depends on nothing heavier than zuspec-ir-core. zuspec-be-xls stays free of zuspec-synth.

## Writing an adapter: checklist

The worked example is `tests/test_engine.py::FakeEngine`.

1. **`accepts`:** try the engine's own front step (lowering). Turn each failure into a
   `Reason`, with the anchor when known.
2. **`build`:**
   1. Lower `roots`, run the engine in `workdir`, and keep its files.
   2. Map each engine node to anchors with a quality (`exact`, `merged`, `derived`,
      `partial` or `unmapped`). Lossy is fine.
   3. Write an `Overlay`. Its header carries `engine_version`, `constraints.to_json()`,
      `anchors.scope_hashes`, `artifacts`, and a `coverage` dict.
   4. Write the core facts the engine knows:
      - `iface.block` (the `BlockSignature` JSON)
      - `sched.*` and `timing.*`, when it schedules
      - `rtl.*`, when it can name registers and nets
3. **Missing namespaces degrade gracefully.** An engine without a schedule simply writes no
   `sched.*` facts; shells, tests and lockstep still work from `iface.block`.

## The XLS adapter

`XlsEngine` (entry point `xls`):
1. lowers with zuspec-be-xls
2. runs `opt_main` and `codegen_main`, with the command line from dv-flow-libsynth's
   `codegen_argv` (the same as `synth.xls.Codegen`; libsynth is imported only inside
   `build`)
3. fixes source comments as libsynth does
4. returns the Verilog with `xls_overlay`'s overlay

fw-hdl's `tests/xls/test_engine_path.py` checks that it produces byte-identical Verilog to
the example flows.
