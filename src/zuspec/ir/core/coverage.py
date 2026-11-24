"""
IR (Intermediate Representation) for coverage definitions.

The model is pure data: coverpoint targets, ``iff`` guards, bin values and
cross-bin selects are IR expressions, never Python callables, so the bc and
SV targets read the same model. A ``CovergroupDef`` is a covergroup type; an
inline covergroup (``covergroup { ... } cg;`` in an action) is an anonymous
type with ``instance_name`` set.
"""
from __future__ import annotations
import dataclasses
import enum
from typing import Optional, List, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .expr import Expr
    from .data_type import DataType


class BinKind(enum.Enum):
    """Which list a bin was declared in."""
    BINS = "bins"
    IGNORE = "ignore_bins"
    ILLEGAL = "illegal_bins"


@dataclasses.dataclass
class CovergroupOptions:
    """Instance-level covergroup options (``option.*``)."""
    name: Optional[str] = None
    weight: int = 1
    goal: int = 100
    comment: str = ""
    at_least: int = 1
    auto_bin_max: int = 64
    per_instance: bool = False
    detect_overlap: bool = False


@dataclasses.dataclass
class TypeOptions:
    """Type-level covergroup options (``type_option.*``)."""
    weight: int = 1
    goal: int = 100
    comment: str = ""
    merge_instances: bool = False


@dataclasses.dataclass
class CovergroupParam:
    """A covergroup type parameter, e.g. ``a`` in ``covergroup cg(bit[4] a)``."""
    name: str
    data_type: Optional['DataType'] = None


@dataclasses.dataclass
class BinDef:
    """One bin declaration of a coverpoint.

    ``values`` holds value expressions and ``ExprRange`` items (either bound
    may be open). ``array`` is set for ``bins b[N]`` (``array_size`` = N)
    and ``bins b[]`` (``array_size`` None): one bin per value or per slice.
    ``with_expr`` is the ``with (...)`` filter, in which the coverpoint's name
    stands for the candidate value; ``values`` empty with ``with_expr`` set is
    the ``bins b = cp with (...)`` form (every value of the coverpoint).
    """
    name: str
    kind: BinKind = BinKind.BINS
    values: List['Expr'] = dataclasses.field(default_factory=list)
    is_default: bool = False
    array: bool = False
    array_size: Optional['Expr'] = None
    with_expr: Optional['Expr'] = None
    loc: Optional[Any] = None


@dataclasses.dataclass
class CoverpointDef:
    """A coverpoint: the sampled expression, its guard and its bins.

    With no ``BinKind.BINS`` entries, bins are automatic (up to
    ``auto_bin_max``).
    """
    name: str
    target: Optional['Expr'] = None
    data_type: Optional['DataType'] = None
    iff: Optional['Expr'] = None
    bins: List[BinDef] = dataclasses.field(default_factory=list)
    auto_bin_max: int = 64
    weight: int = 1
    goal: int = 100
    at_least: int = 1
    comment: str = ""
    loc: Optional[Any] = None

    @property
    def auto_bins(self) -> bool:
        return not any(b.kind is BinKind.BINS for b in self.bins)


@dataclasses.dataclass
class CrossBinDef:
    """A cross bin; ``select`` is a ``binsof``/``intersect`` expression."""
    name: str
    kind: BinKind = BinKind.BINS
    select: Optional['Expr'] = None
    loc: Optional[Any] = None


@dataclasses.dataclass
class CrossDef:
    """A cross over coverpoints of the same covergroup, named in order."""
    name: str
    coverpoints: List[str] = dataclasses.field(default_factory=list)
    bins: List[CrossBinDef] = dataclasses.field(default_factory=list)
    iff: Optional['Expr'] = None
    weight: int = 1
    goal: int = 100
    at_least: int = 1
    comment: str = ""
    loc: Optional[Any] = None

    @property
    def auto_bins(self) -> bool:
        return not any(b.kind is BinKind.BINS for b in self.bins)


@dataclasses.dataclass
class CovergroupDef:
    """A covergroup type, or an inline covergroup instance in an action.

    ``parent`` is the qualified name of the enclosing type (action, flow
    object or struct), if any. Coverpoint targets in an inline covergroup
    refer to the parent's fields through ``TypeExprRefSelf``; in a
    covergroup type they refer to ``params``.

    An instance of a covergroup type (``cg_t cx(x, f);``, LRM 19.2) is
    expanded per instance by the front end: its coverpoints refer to the
    parent's fields, as an inline covergroup's do, and ``type_name`` is the
    type's qualified name (None for an inline covergroup).
    """
    name: str
    instance_name: Optional[str] = None
    type_name: Optional[str] = None
    parent: Optional[str] = None
    params: List[CovergroupParam] = dataclasses.field(default_factory=list)
    coverpoints: List[CoverpointDef] = dataclasses.field(default_factory=list)
    crosses: List[CrossDef] = dataclasses.field(default_factory=list)
    options: CovergroupOptions = dataclasses.field(default_factory=CovergroupOptions)
    type_options: TypeOptions = dataclasses.field(default_factory=TypeOptions)
    loc: Optional[Any] = None

    @property
    def inline(self) -> bool:
        return self.instance_name is not None
