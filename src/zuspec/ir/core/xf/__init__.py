"""IR transforms (``xf``) for ``zuspec-ir-core``.

Per the PSS-lowering architecture (``design/pss-lowering-architecture.md``), all
PSS-lowering transforms live here for now — the PSS→Scenario lowering
(:mod:`.pss_lower`) and (later) the generic ``CoroutineFSMPass`` — keeping the
seams in one place until the body of code justifies a standalone
``zuspec-xf-pss`` plugin.
"""
from .validate import ScenarioValidator, UnsupportedConstructError
from .pss_lower import PSSToScenarioPass
from .coro_fsm import CoroutineFSMPass, FSMForm, FSMBlock
from .pretty import dump_module

__all__ = [
    "ScenarioValidator",
    "UnsupportedConstructError",
    "PSSToScenarioPass",
    "CoroutineFSMPass",
    "FSMForm",
    "FSMBlock",
    "dump_module",
]
