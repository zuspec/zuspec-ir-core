"""Provenance — records how and where an IR node was produced."""
from __future__ import annotations

import dataclasses as dc
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseP


@dc.dataclass
class Provenance:
    """Augments a ``Loc`` with synthesis-pass metadata.

    Carries ``pass_name``, ``source_nodes`` (the IR nodes that were consumed
    to produce this node), and a human-readable ``description``.
    """

    pass_name: str = dc.field()
    source_nodes: List["BaseP"] = dc.field(default_factory=list)
    description: str = dc.field(default="")

    @classmethod
    def chain(
        cls,
        pass_name: str,
        source_nodes: List["BaseP"],
        description: str = "",
    ) -> "Provenance":
        """Create a new ``Provenance`` recording the transformation that produced a node.

        Args:
            pass_name: Name of the pass that created the node.
            source_nodes: IR nodes consumed to produce the new node.
            description: Human-readable description of the transformation.
        """
        return cls(pass_name=pass_name, source_nodes=list(source_nodes), description=description)
