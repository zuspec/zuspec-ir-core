"""
IR (Intermediate Representation) for coverage definitions.
"""
import dataclasses
from typing import Optional, List, Union, Callable, Any


@dataclasses.dataclass
class CovergroupOptions:
    """Instance-level covergroup options."""
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
    """Type-level covergroup options."""
    weight: int = 1
    goal: int = 100
    comment: str = ""
    merge_instances: bool = False


@dataclasses.dataclass
class CovergroupDef:
    """IR for covergroup definition."""
    name: str
    parent_type: Optional[type]
    coverpoints: List['CoverpointDef']
    crosses: List['CrossDef']
    options: CovergroupOptions
    type_options: TypeOptions
    sample_args: List[str] = dataclasses.field(default_factory=list)
    loc: Optional[Any] = None


@dataclasses.dataclass
class CoverpointDef:
    """IR for coverpoint definition."""
    name: str
    field_type: type
    ref: Optional[Callable]
    bins: List['BinDef']
    auto_bins: bool
    auto_bin_max: int
    iff: Optional[Callable] = None
    weight: int = 1
    goal: int = 100
    comment: str = ""
    loc: Optional[Any] = None


@dataclasses.dataclass
class BinDef:
    """IR for single bin definition."""
    name: str
    values: Union[range, List[int], int]
    is_ignore: bool = False
    is_illegal: bool = False
    is_default: bool = False
    iff: Optional[Callable] = None
    loc: Optional[Any] = None


@dataclasses.dataclass
class CrossDef:
    """IR for cross coverage definition."""
    name: str
    coverpoint_refs: List[Callable]
    bins: List['CrossBinDef'] = dataclasses.field(default_factory=list)
    auto_bins: bool = True
    iff: Optional[Callable] = None
    weight: int = 1
    goal: int = 100
    comment: str = ""
    loc: Optional[Any] = None


@dataclasses.dataclass
class CrossBinDef:
    """IR for cross bin definition."""
    name: str
    bin_selects: List[Any]  # BinSelectExpr from phase 3
    iff: Optional[Callable] = None
    loc: Optional[Any] = None


# ---------------------------------------------------------------------------
# PSS-path coverage IR nodes (distinct from Python-native CovergroupDef above)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class PssCoverPoint:
    """IR for a PSS coverpoint declaration.

    Attributes:
        name:              Coverpoint name.
        target_expr:       IR Expr for the sampled variable.
        ignore_bin_exprs:  List of IR Expr for ignore_bins.
    """
    name: str
    target_expr: Any
    ignore_bin_exprs: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class PssCoverCross:
    """IR for a PSS covergroup cross declaration.

    Attributes:
        name:             Cross name.
        coverpoint_names: Names of CoverPoints being crossed.
        ignore_bin_exprs: List of IR Expr for ignore_bins.
    """
    name: str
    coverpoint_names: list  # list[str]
    ignore_bin_exprs: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class PssCoverGroup:
    """IR for a PSS covergroup instance inside an action or struct.

    Attributes:
        instance_name: The covergroup instance name (e.g. ``"cXs_cg"``).
        coverpoints:   List of PssCoverPoint.
        crosses:       List of PssCoverCross.
    """
    instance_name: str
    coverpoints: list  # list[PssCoverPoint]
    crosses: list = dataclasses.field(default_factory=list)  # list[PssCoverCross]
