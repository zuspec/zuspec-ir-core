"""
Lowering interface protocols for Zuspec abstractions.

These ``typing.Protocol`` classes define the *vtable* that each abstraction
class can implement to participate in the Zuspec structured-lowering pipeline.
The design follows the MLIR ``OpInterface`` / CIRCT ``Emittable`` pattern:

* A class declares participation by inheriting :class:`Lowerable` (the
  zero-method marker).
* Richer capabilities are expressed through the four facet protocols below.
* Third-party packages that cannot edit the abstraction source use
  :meth:`~zuspec.ir.core.registry.LoweringRegistry.attach_external_model` —
  the MLIR ``ExternalModel`` pattern.

Dispatch in synthesis passes uses ``isinstance(cls, <Protocol>)`` rather than
string-name inspection, eliminating the fragile ``frozenset`` pattern.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Lowerable(Protocol):
    """Zero-method marker: presence signals participation in structured lowering.

    Analogous to CIRCT's ``Emittable`` interface from
    ``circt/Dialect/Emit/EmitOpInterfaces.td``.

    A class declares participation by inheriting ``Lowerable``, or via
    :meth:`~zuspec.ir.core.registry.LoweringRegistry.attach_external_model`
    when the source cannot be edited.
    """


@runtime_checkable
class ElaboratableInterface(Protocol):
    """An abstraction that controls its own IR node at elaboration time.

    Called by ``DataModelFactory._extract_fields()`` instead of the generic
    ``_process_component()`` path.  The returned
    :class:`~zuspec.ir.core.abstraction_field_ir.AbstractionFieldIR` is opaque
    to the IR-core layer; downstream passes retrieve it from the field and
    dispatch to the registered lowering model.

    Parameters
    ----------
    field_name : str
        Python attribute name on the enclosing component.
    field_index : int
        Zero-based position in the component's field list.
    inst_kwargs : dict
        Keyword args from the ``zdc.inst()`` call (e.g. ``{'WIDTH': 8}``).
    element_type : type, optional
        For generic abstractions such as ``Queue[T]``, the resolved element
        type ``T``.  ``None`` for non-generic abstractions (e.g. ``Counter``).

    Returns
    -------
    AbstractionFieldIR
        An opaque IR node (see :class:`~zuspec.ir.core.abstraction_field_ir.AbstractionFieldIR`).
    """

    @classmethod
    def elaborate_field(
        cls,
        field_name: str,
        field_index: int,
        inst_kwargs: dict,
        element_type: type = None,
    ) -> "AbstractionFieldIR":  # noqa: F821
        ...


@runtime_checkable
class SVEmittableInterface(Protocol):
    """An abstraction that can produce synthesisable SystemVerilog.

    Called by ``AbstractionSVLowerPass`` for each
    :class:`~zuspec.ir.core.abstraction_field_ir.AbstractionFieldIR` node.

    Three capabilities are combined into one interface because they share the
    same structural parameters (extracted once into ``AbstractionFieldIR``):

    * ``sv_module_text()``   — standalone SV module declaration
    * ``sv_instance_text()`` — instantiation snippet with port connections
    * ``rewrite_proc_stmts()`` — transform proc-body AST statements
    """

    @classmethod
    def sv_module_text(cls, field_ir: "AbstractionFieldIR") -> str:  # noqa: F821
        """Return a full SV module declaration for this abstraction.

        Return an empty string if this abstraction does not produce a module
        (e.g. it is inlined into the parent component module).
        """
        ...

    @classmethod
    def sv_instance_text(
        cls,
        field_ir: "AbstractionFieldIR",  # noqa: F821
        parent_prefix: str,
    ) -> str:
        """Return the SV instance connection fragment.

        This snippet is pasted into the parent component module body.
        Return an empty string if inlined rather than instantiated.
        """
        ...

    @classmethod
    def rewrite_proc_stmts(
        cls,
        stmts: list,
        field_ir: "AbstractionFieldIR",  # noqa: F821
    ) -> list:
        """Rewrite proc-body statements that reference this field.

        Returns a new statement list (may be the same object if nothing was
        rewritten).  Called once per ``@proc`` process in the enclosing
        component.

        Raises ``NotImplementedError`` for API calls that cannot be lowered.
        """
        ...


@runtime_checkable
class SVAEmittableInterface(Protocol):
    """An abstraction that self-declares formal SVA properties.

    Called by ``AbstractionFormalPass``.  Two modes:

    - **ASSERT mode**: properties are checked against the DUT.
    - **ASSUME mode**: properties constrain inputs for refinement proofs.
    """

    @classmethod
    def sva_assert_properties(
        cls,
        field_ir: "AbstractionFieldIR",  # noqa: F821
    ) -> list:
        """Return SVA property strings for assertion checking.

        Each string is a complete ``assert property`` statement, e.g.::

            "assert property (@(posedge clk) disable iff (!rst_n) ...);"
        """
        ...

    @classmethod
    def sva_assume_properties(
        cls,
        field_ir: "AbstractionFieldIR",  # noqa: F821
    ) -> list:
        """Return SVA property strings for assumption constraining.

        Companion ``assume property()`` statements for using this abstraction
        as a formal cutpoint (assume model, not DUT-check).
        """
        ...

    @classmethod
    def bmc_depth(cls, field_ir: "AbstractionFieldIR") -> int:  # noqa: F821
        """Minimum BMC depth needed to reach liveness events on this field.

        The formal pass aggregates these across all abstraction fields to set
        the BMC depth bound automatically.  Return ``0`` if this abstraction
        has no liveness event (e.g. a purely combinational abstraction).
        """
        ...

    @classmethod
    def cutpoint_signals(
        cls,
        field_ir: "AbstractionFieldIR",  # noqa: F821
    ) -> list:
        """Signal names that can serve as formal cutpoints for this field.

        An empty list means this abstraction does not offer a cutpoint.
        """
        ...


@runtime_checkable
class CSimEmittableInterface(Protocol):
    """An abstraction that can produce a C++ simulation model.

    Used for co-simulation and rapid pre-synthesis validation.  Optional;
    passes that target C-sim check ``isinstance(cls, CSimEmittableInterface)``.
    """

    @classmethod
    def c_header(cls, field_ir: "AbstractionFieldIR") -> str:  # noqa: F821
        """Return C++ header declarations for this abstraction."""
        ...

    @classmethod
    def c_impl(cls, field_ir: "AbstractionFieldIR") -> str:  # noqa: F821
        """Return C++ implementation code for this abstraction."""
        ...
