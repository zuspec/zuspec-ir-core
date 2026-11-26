
import dataclasses as dc
from typing import List, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .data_type import DataType
    from .package import Package

@dc.dataclass
class Context(object):
    # Map type name to DataType instance
    type_m : Dict[str, "DataType"] = dc.field(default_factory=dict)
    # Map package name to Package (its members in source order; its types
    # are named here, defined in ``type_m``)
    packages : Dict[str, "Package"] = dc.field(default_factory=dict)

