"""DomainNode — abstract base for ISA/design-specific IR nodes."""
from __future__ import annotations

import dataclasses as dc
from abc import abstractmethod
from typing import TYPE_CHECKING, List, Optional, Any

from .base import Base

if TYPE_CHECKING:
    from .connection import Connection
    from .provenance import Provenance


@dc.dataclass(kw_only=True)
class DomainNode(Base):
    """Abstract base for domain-specific synthesis nodes.

    Every ``DomainNode`` subclass must declare:
    - ``lowered_by``: the ``Pass`` instance responsible for lowering it.
    - ``provenance``: a ``Provenance`` recording how this node was introduced.

    Agnostic (ISA-unaware) passes must not call any method other than the
    inherited ``Base`` visitor interface.  They may inspect ``lowered_by``
    to decide whether to skip or forward the node.
    """

    lowered_by: Optional[Any] = dc.field(default=None)
    provenance: Optional["Provenance"] = dc.field(default=None)

    @abstractmethod
    def inputs(self) -> List["Connection"]:
        """Return the list of input ``Connection`` objects for this node."""

    @abstractmethod
    def outputs(self) -> List["Connection"]:
        """Return the list of output ``Connection`` objects produced by this node."""
