from __future__ import annotations
import dataclasses as dc
import enum
from typing import List, Optional, Protocol, TYPE_CHECKING, Iterator, Any
from .base import Base
from .expr import Expr

if TYPE_CHECKING:
    from .fields import Field
    from .stmt import Stmt, Arguments
    from .activity import ActivitySequenceBlock

class ProcessKind(enum.Enum):
    """Kind of hardware process"""
    COMB = enum.auto()  # Combinational logic
    SYNC = enum.auto()  # Synchronous (clocked) logic
    WIRE = enum.auto()  # Continuous assignment (from @property getter)

@dc.dataclass(kw_only=True)
class DataType(Base):
    name : Optional[str] = dc.field(default=None)
    py_type : Optional[Any] = dc.field(default=None)  # Reference to original Python type

@dc.dataclass(kw_only=True)
class DataTypePyObj(Base): 
    """Opaque Python object"""
    ...

@dc.dataclass(kw_only=True)
class DataTypeInt(DataType):
    bits : int = dc.field(default=-1)
    signed : bool = dc.field(default=True)

@dc.dataclass(kw_only=True)
class DataTypeUptr(DataType):
    """Platform-sized unsigned pointer type.
    
    The width is determined at runtime based on the platform's pointer size.
    This is semantically equivalent to an unsigned integer large enough to hold
    an address value (typically 32 or 64 bits depending on the platform).
    """
    
    @staticmethod
    def get_platform_width() -> int:
        """Get the platform's pointer size in bits."""
        import struct
        return struct.calcsize('P') * 8

@dc.dataclass(kw_only=True)
class DataTypeStruct(DataType):
    """Structs are pure-data types. 
    - methods and constraints may be applied
    - may inherit from a base

    - use 'Optional' in input to identify ref vs value
    - construct by default (?)
    - have boxed types to permit memory management?
    --> consume semantics
    """
    super : Optional[DataType] = dc.field()
    fields : List[Field] = dc.field(default_factory=list)
    functions : List = dc.field(default_factory=list)
    is_abstract : bool = dc.field(default=False)
#    constraints

@dc.dataclass(kw_only=True)
class DataTypeClass(DataTypeStruct):
    """Classes are a polymorphic extension of Structs"""
    activity_ir: Optional['ActivitySequenceBlock'] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class DataTypeAction(DataTypeClass):
    """An action type (subclass of zdc.Action[T]).

    Attributes
    ----------
    comp_type_name:
        Name of the owning component type T from ``Action[T]``.
    body_stmts:
        IR statements from the ``body()`` method (populated by DataModelFactory).
        When the action call is inlined into a parent coroutine these stmts are
        inserted verbatim (after translation of ``self`` / ``self.comp`` refs).
    body_override_source:
        Name of the ``@zdc.extend`` class that provided a body() override for
        this action, or ``None`` when the body comes from the base class itself.
        Set by :class:`~zuspec.dataclasses.data_model_factory.DataModelFactory`
        when an extension with ``__is_body_override__ = True`` is discovered.
        Useful for tooling and ECO audit trails.
    """
    comp_type_name: Optional[str] = dc.field(default=None)
    body_stmts: List = dc.field(default_factory=list)  # List[Stmt]
    static_methods: List = dc.field(default_factory=list)  # List[Function]
    body_override_source: Optional[str] = dc.field(default=None)
    constraint_set: Optional[Any] = dc.field(default=None)  # ActionConstraintSet | None


@dc.dataclass(kw_only=True)
class DataTypeComponent(DataTypeClass):
    """Components are structural building blocks that can have ports, exports, 
    and bindings. The bind_map captures connections between ports/exports."""
    bind_map : List['Bind'] = dc.field(default_factory=list)
    sync_processes : List[Function] = dc.field(default_factory=list)
    comb_processes : List[Function] = dc.field(default_factory=list)
    wire_processes : List[Function] = dc.field(default_factory=list)
    proc_processes : List[Function] = dc.field(default_factory=list)
    # New-style pipeline IRs (populated when @zdc.pipeline + @zdc.stage are present)
    pipeline_root_ir: Optional[Any] = dc.field(default=None)   # PipelineRootIR | None
    stage_method_irs: List[Any]     = dc.field(default_factory=list)  # List[StageMethodIR]
    sync_method_irs:  List[Any]     = dc.field(default_factory=list)  # List[SyncMethodIR]
    # Clock/reset domain (None → inherit from parent at elaboration time)
    clock_domain:     Optional[Any] = dc.field(default=None)   # ClockDomain | None
    reset_domain:     Optional[Any] = dc.field(default=None)   # ResetDomain | None


@dc.dataclass(kw_only=True)
class DataTypeExtern(DataTypeComponent):
    """Extern component signature.

    Represents an externally-implemented component/module.
    """

    extern_name: Optional[str] = dc.field(default=None)

if TYPE_CHECKING:
    from .fields import Bind

@dc.dataclass(kw_only=True)
class DataTypeExpr(DataType):
    expr : Expr

@dc.dataclass(kw_only=True)
class DataTypeEnum(DataType):
    """Enum data type with named integer members.

    ``items`` maps member name → integer value (in declaration order).
    Values are auto-assigned (starting from 0, incrementing by 1) when no
    explicit value is given, matching PSS §7.5 semantics.

    ``width`` is the bit width of the enum type. If not specified, it is
    inferred from the number of members (ceiling log2 of len(items)+1).
    """
    items: dict = dc.field(default_factory=dict)  # OrderedDict[str, int]
    width: int = dc.field(default=0)  # 0 = auto-infer from items

@dc.dataclass(kw_only=True)
class DataTypeString(DataType): ...

@dc.dataclass(kw_only=True)
class DataTypeChandle(DataType):
    """Opaque C handle type (PSS §7.7).

    Represents a foreign-language handle (e.g., a C pointer) that PSS code
    can hold and pass around but cannot dereference.  Maps to ``ctypes.c_void_p``
    or plain ``int`` in a Python runtime.
    """

@dc.dataclass(kw_only=True)
class DataTypeList(DataType):
    """Dynamic list collection type (PSS §7.9.3).

    ``list<element_type>`` in PSS.  Maps to ``List[...]`` in Python.
    """
    element_type: Optional[DataType] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class DataTypeArray(DataType):
    """Fixed-size array collection type (PSS §7.9.2).

    ``array<element_type, size>`` or ``element_type name[size]`` in PSS.
    Maps to a fixed-length ``List[...]`` in Python.
    A size of -1 means the size is not statically known.
    """
    element_type: Optional[DataType] = dc.field(default=None)
    size: int = dc.field(default=-1)

@dc.dataclass(kw_only=True)
class DataTypeMap(DataType):
    """Associative map collection type (PSS §7.9.4).

    ``map<key_type, value_type>`` in PSS.  Maps to ``Dict[...]`` in Python.
    """
    key_type: Optional[DataType] = dc.field(default=None)
    value_type: Optional[DataType] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class DataTypeSet(DataType):
    """Unordered set collection type (PSS §7.9.5).

    ``set<element_type>`` in PSS.  Maps to ``Set[...]`` in Python.
    """
    element_type: Optional[DataType] = dc.field(default=None)



@dc.dataclass(kw_only=True)
class DataTypeLock(DataType):
    """Represents a Lock (mutex) type for synchronization"""
    pass

class DataTypeEvent(DataType):
    """Represents an Event type for interrupt/callback handling"""
    pass

@dc.dataclass(kw_only=True)
class DataTypeMemory(DataType):
    """Represents a Memory type - storage for data elements"""
    element_type : Optional[DataType] = dc.field(default=None)
    size : int = dc.field(default=1024)

@dc.dataclass(kw_only=True)
class DataTypeAddressSpace(DataType):
    """Represents an AddressSpace - software view of memory and registers"""
    pass

@dc.dataclass(kw_only=True)
class DataTypeAddrHandle(DataType):
    """Represents an AddrHandle - pointer abstraction for memory access"""
    pass

@dc.dataclass(kw_only=True)
class DataTypeProtocol(DataType):
    """Represents a Python Protocol (interface definition)"""
    methods : List['Function'] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class Function(Base):
    """Represents a method or function"""
    name : str = dc.field()
    args : 'Arguments' = dc.field(default=None)
    body : List['Stmt'] = dc.field(default_factory=list)
    returns : Optional[DataType] = dc.field(default=None)
    is_async : bool = dc.field(default=False)
    metadata : dict = dc.field(default_factory=dict)
    is_invariant : bool = dc.field(default=False)
    process_kind : Optional[ProcessKind] = dc.field(default=None)
    sensitivity_list : List[Expr] = dc.field(default_factory=list)
    # Import/export information
    is_import : bool = dc.field(default=False)
    is_target : bool = dc.field(default=False)  # import target
    is_solve : bool = dc.field(default=False)   # import solve
    # Leading comment block from Python source (docstring or # lines above decorator)
    comment : Optional[str] = dc.field(default=None)

@dc.dataclass(kw_only=True)
class Process(Base):
    """Represents a process (@process decorated method)"""
    name : str = dc.field()
    body : List['Stmt'] = dc.field(default_factory=list)

@dc.dataclass(kw_only=True)
class DataTypeRef(DataType):
    """Reference to another type by name (for forward references)"""
    ref_name : str = dc.field()


@dc.dataclass(kw_only=True)
class DataTypeGetIF(DataType):
    """Represents a GetIF interface - consumer side of a channel"""
    element_type : Optional[DataType] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class DataTypePutIF(DataType):
    """Represents a PutIF interface - producer side of a channel"""
    element_type : Optional[DataType] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class DataTypeTuple(DataType):
    """Represents a fixed-size Tuple field."""
    element_type : Optional[DataType] = dc.field(default=None)
    size : int = dc.field(default=0)
    # Optional implementation/factory type to construct elements
    elem_factory : Optional['DataType'] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class DataTypeChannel(DataType):
    """Represents a TLM Channel - bidirectional communication channel"""
    element_type : Optional[DataType] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class DataTypeTupleReturn(DataType):
    """Return type of a multi-value function call (e.g. regfile.read_all()).

    Used to lower tuple-unpack assignments ``a, b = f(...)`` to a temporary
    struct variable and individual field extractions ``a = _tmp.v0; b = _tmp.v1``.
    The C struct type is ``_zsp_tupleN_t`` where N is the arity.
    """
    arity: int = dc.field(default=2)


@dc.dataclass(kw_only=True)
class DataTypeClaimPool(DataType):
    """Represents a ``ClaimPool[ElemType]`` field.

    Carries the element component type name so that the SW backend can emit
    the element struct inline and resolve calls through claim handles.
    """
    elem_type_name: str = dc.field(default="")


# =============================================================================
# Template Parameter Support (for parameterized types like reg_c<R, ACC, SZ>)
# =============================================================================

class TemplateParamKind(enum.Enum):
    """Kind of template parameter"""
    TYPE = enum.auto()    # type parameter (e.g., type R)
    VALUE = enum.auto()   # int/bit parameter (e.g., int SZ)
    ENUM = enum.auto()    # enum parameter (e.g., reg_access ACC)


@dc.dataclass(kw_only=True)
class TemplateParam(Base):
    """Template parameter declaration"""
    name : str = dc.field()
    kind : TemplateParamKind = dc.field()


@dc.dataclass(kw_only=True)
class TemplateParamType(TemplateParam):
    """Type template parameter (e.g., 'type R')"""
    constraint_type : Optional[DataType] = dc.field(default=None)  # Base constraint
    default_value : Optional[DataType] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class TemplateParamValue(TemplateParam):
    """Value template parameter (e.g., 'int SZ')"""
    value_type : DataType = dc.field()  # int, bit, etc.
    default_value : Optional[Expr] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class TemplateParamEnum(TemplateParam):
    """Enum template parameter (e.g., 'reg_access ACC')"""
    enum_type : DataTypeEnum = dc.field()
    default_value : Optional[str] = dc.field(default=None)


# =============================================================================
# Template Argument Support (actual parameters in instantiation)
# =============================================================================

@dc.dataclass(kw_only=True)
class TemplateArg(Base):
    """Template argument (actual parameter in instantiation)"""
    param_name : str = dc.field()


@dc.dataclass(kw_only=True)
class TemplateArgType(TemplateArg):
    """Type argument"""
    type_value : DataType = dc.field()


@dc.dataclass(kw_only=True)
class TemplateArgValue(TemplateArg):
    """Value argument"""
    value_expr : Expr = dc.field()


@dc.dataclass(kw_only=True)
class TemplateArgEnum(TemplateArg):
    """Enum argument"""
    enum_value : str = dc.field()


# =============================================================================
# Parameterized Types
# =============================================================================

@dc.dataclass(kw_only=True)
class DataTypeParameterized(DataType):
    """Base type that can be parameterized with template arguments
    
    Represents the uninstantiated template (e.g., reg_c itself)
    """
    template_params : List[TemplateParam] = dc.field(default_factory=list)


@dc.dataclass(kw_only=True)
class DataTypeSpecialized(DataType):
    """Instantiated template with concrete arguments
    
    Example: reg_c<bit[32], READWRITE, 32>
    
    Template parameter values are retrievable from specialized types
    for downstream tooling to reason about register widths, access modes, etc.
    """
    base_template : DataTypeParameterized = dc.field()
    template_args : List[TemplateArg] = dc.field(default_factory=list)
    specialized_name : Optional[str] = dc.field(default=None)  # e.g., "reg_c_bit32_READWRITE_32"
    
    def get_template_arg(self, param_name: str) -> Optional[TemplateArg]:
        """Retrieve template argument by parameter name"""
        for arg in self.template_args:
            if arg.param_name == param_name:
                return arg
        return None
    
    def get_template_arg_value(self, param_name: str) -> Any:
        """Retrieve the actual value of a template argument
        
        Returns:
            - DataType for type parameters
            - Expr for value parameters  
            - str for enum parameters
            - None if parameter not found
        """
        arg = self.get_template_arg(param_name)
        if arg is None:
            return None
        if isinstance(arg, TemplateArgType):
            return arg.type_value
        elif isinstance(arg, TemplateArgValue):
            return arg.value_expr
        elif isinstance(arg, TemplateArgEnum):
            return arg.enum_value
        return None


# =============================================================================
# Register-Specific Types
# =============================================================================

@dc.dataclass(kw_only=True)
class DataTypeRegister(DataTypeComponent):
    """Register component type (specialization of reg_c)
    
    Represents an instantiated register component with resolved template parameters.
    For eager specialization, all template parameters are resolved and stored directly
    as fields for easy access by downstream tools.
    """
    # Core register parameters (extracted from template args for direct access)
    register_value_type : DataType = dc.field()  # Type R parameter
    access_mode : str = dc.field(default="READWRITE")  # ACC parameter (READWRITE, READONLY, WRITEONLY)
    size_bits : int = dc.field()  # SZ2 parameter
    
    # Template information (for tools that need full template context)
    base_template : Optional[DataTypeParameterized] = dc.field(default=None)  # Reference to reg_c template
    template_args : List[TemplateArg] = dc.field(default_factory=list)  # Actual arguments used
    
    # Register-specific metadata
    is_pure : bool = dc.field(default=True)  # Registers should be pure components
    
    # SystemRDL compatibility fields
    systemrdl_regwidth : Optional[int] = dc.field(default=None)  # Power-of-2 width for SystemRDL
    systemrdl_accesswidth : Optional[int] = dc.field(default=None)
    
    # Inherited from DataTypeComponent:
    # - fields: register fields (from struct R if applicable)
    # - functions: read(), write(), read_val(), write_val(), etc.
    
    def get_register_param(self, param_name: str) -> Any:
        """Convenience method to retrieve template parameter values
        
        Args:
            param_name: 'R', 'ACC', or 'SZ2'/'SZ'
            
        Returns:
            - register_value_type for 'R'
            - access_mode string for 'ACC'
            - size_bits int for 'SZ2' or 'SZ'
        """
        if param_name == 'R':
            return self.register_value_type
        elif param_name == 'ACC':
            return self.access_mode
        elif param_name == 'SZ2' or param_name == 'SZ':
            return self.size_bits
        return None
    
    def compute_systemrdl_width(self) -> int:
        """Compute SystemRDL-compatible width (next power of 2)"""
        import math
        if self.size_bits < 8:
            return 8
        return 1 << (self.size_bits - 1).bit_length()


@dc.dataclass(kw_only=True)
class DataTypeRegisterGroup(DataTypeComponent):
    """Register group component type (specialization of reg_group_c)
    
    Aggregates registers and sub-groups with offset management.
    """
    # Inherited fields list contains register instances
    
    # Offset tracking
    offset_map : dict = dc.field(default_factory=dict)  # field_name -> offset
    
    # Register group is always pure
    is_pure : bool = dc.field(default=True)
    
    # Address handle association
    has_address_handle : bool = dc.field(default=False)


# =============================================================================
# Interface-Protocol Types (IfProtocol, Completion, Queue)
# =============================================================================

@dc.dataclass(kw_only=True)
class IfProtocolProperties:
    """Protocol-level properties captured from IfProtocol class kwargs.

    These drive signal generation, protocol checking, and RTL template
    selection during synthesis.
    """
    req_always_ready:      bool          = dc.field(default=False)
    req_registered:        bool          = dc.field(default=False)
    resp_always_valid:     bool          = dc.field(default=False)
    fixed_latency:         Optional[int] = dc.field(default=None)
    resp_has_backpressure: bool          = dc.field(default=False)
    max_outstanding:       int           = dc.field(default=1)
    in_order:              bool          = dc.field(default=True)
    initiation_interval:   int           = dc.field(default=1)


@dc.dataclass(kw_only=True)
class IfProtocolType(DataType):
    """Type of an IfProtocol-derived class (port or export field type)."""
    cls:        Optional[Any]              = dc.field(default=None)  # the user's protocol class
    properties: Optional[IfProtocolProperties] = dc.field(default=None)


@dc.dataclass(kw_only=True)
class CompletionType(DataType):
    """Type of a ``zdc.Completion[T]`` field or local variable."""
    payload_type: Optional[DataType] = dc.field(default=None)  # T


@dc.dataclass(kw_only=True)
class QueueType(DataType):
    """Type of a ``zdc.Queue[T]`` component field or local."""
    element_type: Optional[DataType] = dc.field(default=None)  # T
    depth:        int                 = dc.field(default=1)


