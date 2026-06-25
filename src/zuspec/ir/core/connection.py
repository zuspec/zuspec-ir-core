"""Connection — abstract base and concrete signal/bundle/method-port types."""
from __future__ import annotations

import dataclasses as dc
from typing import List


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
    signal: str = dc.field()


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
    """

    module: str = dc.field()
    name: str = dc.field()
    connections: List[PortConnection] = dc.field(default_factory=list)
