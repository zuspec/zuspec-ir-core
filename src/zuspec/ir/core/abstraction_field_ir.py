"""
AbstractionFieldIR — the shared opaque IR node for abstraction fields.

Produced by :meth:`~zuspec.ir.core.interfaces.ElaboratableInterface.elaborate_field`
and consumed by synthesis/formal passes through the
:class:`~zuspec.ir.core.registry.LoweringRegistry`.

Design note: analogous to MLIR's ``TypedAttr`` — a typed wrapper around an
otherwise opaque blob, where the ``spec_type_name`` tag drives dispatch.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class AbstractionFieldIR:
    """Opaque IR node produced by ``ElaboratableInterface.elaborate_field()``.

    The ``spec_type_name`` key is used by :class:`LoweringRegistry` to look up
    the registered model.  ``ir_node`` is typed only by the package that owns
    the abstraction spec.

    Attributes
    ----------
    spec_type_name : str
        Class name of the abstraction, e.g. ``"Counter"`` or ``"RegisterFile"``.
    field_name : str
        Python attribute name on the enclosing component, e.g. ``"cnt"``.
    field_index : int
        Zero-based position in the component's field list.
    py_cls : type
        The Python class (e.g. ``Counter``).
    inst_kwargs : dict
        Keyword args from the ``zdc.inst()`` call, e.g. ``{'WIDTH': 8}``.
    ir_node : Any
        Abstraction-specific IR payload (e.g. ``CounterIR``, ``MmrRegFileDeclIR``).
    """

    spec_type_name: str
    field_name: str
    field_index: int
    py_cls: type
    inst_kwargs: dict
    ir_node: Any

    @property
    def is_abstraction_field(self) -> bool:
        """Always ``True``; used by ``_is_hierarchical()`` guard."""
        return True

    @property
    def name(self) -> str:
        """Alias for ``field_name``; satisfies the ``Field.name`` duck-type contract."""
        return self.field_name
