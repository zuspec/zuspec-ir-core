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
