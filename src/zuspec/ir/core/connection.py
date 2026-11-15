"""Connection — abstract base and concrete signal/bundle/method-port types."""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List


@dc.dataclass
class Connection:
    """Abstract base for all connection types."""

    name: str = dc.field()


@dc.dataclass
class Signal(Connection):
    """A scalar or vector wire.

    Args:
        name: Signal name.
        width: Bit-width (1 = single bit).
    """

    width: int = dc.field(default=1)


@dc.dataclass
class Bundle(Connection):
    """A named group of ``Connection`` objects.

    Args:
        name: Bundle name.
        members: Ordered list of member connections.
    """

    members: List[Connection] = dc.field(default_factory=list)


@dc.dataclass
class MethodInterface(Connection):
    """A method-port pair with request and response signal lists.

    Args:
        name: Interface name.
        req: Request signals (caller → callee).
        resp: Response signals (callee → caller).
    """

    req: List[Signal] = dc.field(default_factory=list)
    resp: List[Signal] = dc.field(default_factory=list)


@dc.dataclass
class PortConnection:
    """One ``.port(signal)`` of a structural module instantiation.

    Args:
        port: The instantiated module's port name.
        signal: The enclosing module's signal/net wired to it.
    """

    port: str = dc.field()
    #: A net or port of the enclosing module, by name; an element of an
    #: array net (``ExprHierarchical`` of one element with a ``subscript``);
    #: or an ``Expr``: a constant tied to an input pin. None leaves an output
    #: pin open.
    signal: Any = dc.field()


@dc.dataclass
class ModuleInstance:
    """A *structural* sub-module instantiation, self-contained for emission.

    Unlike ``inst()`` component fields (which resolve the instantiated type from
    the IR ``Context``), a ``ModuleInstance`` names its target module as a string
    and carries an explicit port→signal map.  This lets a structural top assemble
    modules that are **not** IR components — e.g. a text-emitted regblock core —
    alongside ones that are (an IR-lowered engine).  ``DataTypeComponent`` holds a
    list of these in ``module_instances``; the SV back end emits one
    ``<module> <name> ( .port(signal), ... );`` per instance.

    Args:
        module: Target module (the instantiated module's name).
        name: Instance name.
        connections: Ordered ``.port(signal)`` connections.
        parameters: Ordered ``#(.NAME(value))`` overrides; a string value is
            emitted as written (e.g. ``"16"``), an ``Expr`` or ``DataType`` value
            (a type parameter's) is generated.
    """

    module: str = dc.field()
    name: str = dc.field()
    connections: List[PortConnection] = dc.field(default_factory=list)
    parameters: Dict[str, Any] = dc.field(default_factory=dict)


@dc.dataclass
class NetAssign:
    """A continuous assignment of a flat structural module: ``assign lhs =
    rhs``.

    Args:
        lhs: The driven port or net, by name, or an element of an array net:
            ``ExprHierarchical`` of one element with a ``subscript``.
        rhs: The driving port or net, by name, an element of an array net
            (as for ``lhs``), or an ``Expr`` constant or parameter expression.
    """

    lhs: Any = dc.field()
    rhs: Any = dc.field()


@dc.dataclass
class GenerateFor:
    """A generate loop of a structural component (``for (genvar var = start;
    cond; var++) begin : label ... end``).

    Used in both structural forms (core-layer1.md §6.3, Z5):

    - *structural*: ``items`` are ``Bind``\\ s (connections made in the loop;
      a path element's ``subscript`` may use the genvar), nested
      ``GenerateFor``/``GenerateIf``, and, in a ``GenerateIf``, child
      ``Field``\\ s: children (or instance arrays) that exist only when the
      block does. Connections to such a child are made in its block or one
      inside it;
    - *flat*: ``items`` are ``NetAssign``\\ s, ``ModuleInstance``\\ s, nested
      generate blocks, and ``Field`` nets declared in the block (the nets
      of a child declared there).

    In expressions the genvar is ``ExprRefLocal(name=var)`` and a template
    parameter of the component ``ExprRefParam(name)``.

    Args:
        var: The genvar's name.
        start: Its first value (an ``Expr``).
        cond: The continuation condition (an ``Expr`` over the genvar).
        label: The generate block's name.
        items: The block's contents, as above.
    """

    var: str = dc.field()
    start: Any = dc.field()
    cond: Any = dc.field()
    label: str = dc.field(default="")
    items: List[Any] = dc.field(default_factory=list)


@dc.dataclass
class GenerateIf:
    """A generate ``if (cond) begin : label ... end else begin ... end`` of a
    structural component; ``items``/``orelse`` hold what a
    :class:`GenerateFor`'s ``items`` hold. ``cond`` is an expression of the
    component's template parameters (and enclosing genvars)."""

    cond: Any = dc.field()
    items: List[Any] = dc.field(default_factory=list)
    orelse: List[Any] = dc.field(default_factory=list)
    label: str = dc.field(default="")
