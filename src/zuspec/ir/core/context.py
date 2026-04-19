
import dataclasses as dc
from typing import List, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .data_type import DataType

@dc.dataclass
class Context(object):
    # Map type name to DataType instance
    type_m : Dict[str, "DataType"] = dc.field(default_factory=dict)

