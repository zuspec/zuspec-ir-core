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

    ``source_nodes`` holds *live* IR nodes, which makes a provenance record a
    back-edge out of the tree a consumer thinks it is walking: a generic
    dataclass walk that descends into it reaches the source node's whole
    subtree, and for a construct that instantiates itself, reaches a cycle.
    Passes that only need to *name* their sources should therefore use
    ``source_names``, which cannot be walked into.
    """

    pass_name: str = dc.field()
    source_nodes: List["BaseP"] = dc.field(default_factory=list)
    description: str = dc.field(default="")

    #: The source constructs by name, innermost or outermost per the pass's own
    #: convention. Inert by construction, so annotating a node with it cannot
    #: change what walking that node finds -- which is the difference between a
    #: provenance record and a graph edge.
    source_names: List[str] = dc.field(default_factory=list)

    #: Which *use* of ``source_nodes`` this node came from, when one pass
    #: instantiates the same source more than once. Without it, two
    #: instantiations of one declaration carry indistinguishable provenance and
    #: a consumer cannot tell "these came from the same place" from "these came
    #: from the same declaration, twice". Numbered per pass, from 1.
    site: Optional[int] = dc.field(default=None)

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
