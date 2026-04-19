"""
The zuspec.ir module defines a set of classes that provide a type model
to represent time-consuming behaviors. The data model targets behavioral
models of hardware-centric systems.

Several conventions are used by this data model, and extensions, to 
textually represent content within the datamodel and constraints on
that content.

"""
import dataclasses as dc
from typing import dataclass_transform

def profile(modname, super=None):
    """Register a profile"""
    from .profile_rgy import ProfileRgy
    ProfileRgy.register_profile(modname, super)


from .base import Base, BaseP
from .context import Context
from .visitor import Visitor
from .json_converter import JsonConverter

@dataclass_transform()
def visitor_dataclass(pmod, *args, **kwargs):
    """Decorator for datamodel Visitor class"""
    def closure(T):
        c = dc.dataclass(T, *args, **kwargs)
        setattr(c, "__new__", lambda cls,pmod=pmod: Visitor.__new__(cls,pmod))
        return c
    return closure

def visitor(pmod, *args, **kwargs):
    """Decorator for non-datamodel Visitor class"""
    def closure(T):
        setattr(T, "__new__", lambda cls,pmod=pmod: Visitor.__new__(cls,pmod))
        return T
    return closure

def json_converter(pmod, *args, **kwargs):
    """Decorator for JsonConverter class"""
    def closure(T):
        setattr(T, "__new__", lambda cls,pmod=pmod: JsonConverter.__new__(cls,pmod))
        return T
    return closure


# M0 additions: provenance, pass infrastructure, connections, RTL IR
from .provenance import Provenance
from .domain_node import DomainNode
from .connection import Connection, Signal, Bundle, MethodInterface

# Re-export data model types
from .fields import Bind, BindSet, Field, FieldInOut, FieldKind, SignalDirection
from .data_type import (
    DataType, DataTypeInt, DataTypeUptr, DataTypeStruct, DataTypeClass, DataTypeAction, DataTypeComponent, DataTypeExtern,
    DataTypeExpr, DataTypeEnum, DataTypeString, DataTypeChandle,
    DataTypeList, DataTypeArray, DataTypeMap, DataTypeSet,
    DataTypeLock, DataTypeEvent, DataTypeMemory,
    DataTypeAddressSpace, DataTypeAddrHandle, DataTypeProtocol, DataTypeRef,
    DataTypeGetIF, DataTypePutIF, DataTypeChannel, DataTypeTuple, DataTypeTupleReturn,
    DataTypeClaimPool,
    Function, Process, ProcessKind,
    # Template support
    TemplateParamKind, TemplateParam, TemplateParamType, TemplateParamValue, TemplateParamEnum,
    TemplateArg, TemplateArgType, TemplateArgValue, TemplateArgEnum,
    DataTypeParameterized, DataTypeSpecialized,
    # Register support
    DataTypeRegister, DataTypeRegisterGroup,
    # Interface-protocol types
    IfProtocolProperties, IfProtocolType, CompletionType, QueueType,
)
from .expr import (
    Expr, BinOp, UnaryOp, BoolOp, CmpOp, AugOp,
    ExprBin, ExprRef, ExprConstant, TypeExprRefSelf, ExprRefField,
    ExprRefParam, ExprRefLocal, ExprRefUnresolved, ExprRefPy,
    ExprRefBottomUp, ExprUnary, ExprBool, ExprCompare,
    ExprAttribute, ExprSlice, ExprSubscript, ExprCall, Keyword, ExprAwait,
    # PSS-specific expressions (Phase 1)
    ExprRange, ExprRangeList, ExprIn, ExprStructField, ExprStructLiteral,
    # Advanced expressions (Phase 3)
    ExprCast, ExprStringMethod, ExprHierarchical, ExprHierarchicalElem,
    ExprStaticRef, ExprCompileHas, ExprNull,
    # Interface-protocol expressions
    CompletionAwaitExpr, QueueGetExpr,
)
from .expr_phase2 import (
    ExprList, ExprTuple, ExprDict, ExprSet, Comprehension, ExprListComp,
    ExprDictComp, ExprSetComp, ExprGeneratorExp, ExprIfExp, ExprLambda, ExprNamedExpr,
    ExprJoinedStr, ExprFormattedValue
)
from .stmt import (
    Stmt, StmtExpr, StmtAssign, StmtAnnAssign, StmtAugAssign, StmtReturn, StmtIf, StmtFor,
    StmtWhile, StmtBreak, StmtContinue, StmtPass, StmtRaise, StmtAssert, Alias, Arg, Arguments,
    StmtAssume, StmtCover, StmtUnique,
    WithItem, StmtWith, StmtExceptHandler, StmtTry, TypeIgnore, Module,
    StmtMatch, StmtMatchCase, Pattern, PatternValue, PatternAs, PatternOr, PatternSequence,
    # PSS-specific statements
    StmtRepeat, StmtRepeatWhile, StmtForeach, StmtYield, StmtRandomize,
    # Interface-protocol statements
    SpawnStmt, SelectStmt, CompletionSetStmt, QueuePutStmt,
)
from .activity import (
    JoinSpec,
    ActivityStmt,
    ActivitySequenceBlock,
    ActivityParallel,
    ActivitySchedule,
    ActivityAtomic,
    ActivityTraversal,
    ActivityAnonTraversal,
    ActivitySuper,
    ActivityRepeat,
    ActivityDoWhile,
    ActivityWhileDo,
    ActivityForeach,
    ActivityReplicate,
    SelectBranch,
    ActivitySelect,
    ActivityIfElse,
    MatchCase,
    ActivityMatch,
    ActivityConstraint,
    ActivityBind,
)

__all__ = [
    "profile","Base","BaseP","Visitor","JsonConverter","json_converter",
    "Bind","BindSet","Field","FieldInOut","FieldKind","SignalDirection",
    "DataType","DataTypeInt","DataTypeUptr","DataTypeStruct","DataTypeClass","DataTypeAction","DataTypeComponent","DataTypeExtern",
    "DataTypeExpr","DataTypeEnum","DataTypeString","DataTypeChandle",
    "DataTypeList","DataTypeArray","DataTypeMap","DataTypeSet",
    "DataTypeLock","DataTypeEvent","DataTypeMemory",
    "DataTypeAddressSpace","DataTypeAddrHandle","DataTypeProtocol","DataTypeRef",
    "DataTypeGetIF","DataTypePutIF","DataTypeChannel","DataTypeTuple","DataTypeTupleReturn",
    "DataTypeClaimPool",
    "Function","Process","ProcessKind",
    # Template support
    "TemplateParamKind","TemplateParam","TemplateParamType","TemplateParamValue","TemplateParamEnum",
    "TemplateArg","TemplateArgType","TemplateArgValue","TemplateArgEnum",
    "DataTypeParameterized","DataTypeSpecialized",
    # Register support
    "DataTypeRegister","DataTypeRegisterGroup",
    # Interface-protocol types
    "IfProtocolProperties","IfProtocolType","CompletionType","QueueType",
    "Expr","BinOp","UnaryOp","BoolOp","CmpOp","AugOp","ExprBin","ExprRef","ExprConstant",
    "TypeExprRefSelf","ExprRefField","ExprRefParam","ExprRefLocal","ExprRefUnresolved",
    "ExprRefPy","ExprRefBottomUp","ExprUnary",
    "ExprBool","ExprCompare","ExprAttribute","ExprSlice","ExprSubscript","ExprCall","Keyword","ExprAwait",
    # PSS-specific expressions
    "ExprRange","ExprRangeList","ExprIn","ExprStructField","ExprStructLiteral",
    # Advanced expressions (Phase 3)
    "ExprCast","ExprStringMethod","ExprHierarchical","ExprHierarchicalElem",
    "ExprStaticRef","ExprCompileHas","ExprNull",
    # Interface-protocol expressions
    "CompletionAwaitExpr","QueueGetExpr",
    "Stmt","StmtExpr","StmtAssign","StmtAnnAssign","StmtAugAssign","StmtReturn","StmtIf","StmtFor","StmtWhile",
    "StmtBreak","StmtContinue","StmtPass","StmtRaise","StmtAssert","StmtAssume","StmtCover","StmtUnique","Alias","Arg","Arguments",
    # PSS-specific statements
    "StmtRepeat","StmtRepeatWhile","StmtForeach","StmtYield","StmtRandomize",
    # Interface-protocol statements
    "SpawnStmt","SelectStmt","CompletionSetStmt","QueuePutStmt",
"ExprList","ExprTuple","ExprDict","ExprSet","Comprehension","ExprListComp","ExprDictComp",
"ExprSetComp","ExprGeneratorExp","ExprIfExp","ExprLambda","ExprNamedExpr",
"WithItem","StmtWith","StmtExceptHandler","StmtTry","TypeIgnore","Module",
"ExprJoinedStr","ExprFormattedValue","StmtMatch","StmtMatchCase","Pattern","PatternValue","PatternAs","PatternOr","PatternSequence",
"Context",
    # Activity IR nodes
    "JoinSpec",
    "ActivityStmt","ActivitySequenceBlock","ActivityParallel","ActivitySchedule","ActivityAtomic",
    "ActivityTraversal","ActivityAnonTraversal","ActivitySuper",
    "ActivityRepeat","ActivityDoWhile","ActivityWhileDo","ActivityForeach","ActivityReplicate",
    "SelectBranch","ActivitySelect",
    "ActivityIfElse","MatchCase","ActivityMatch",
    "ActivityConstraint","ActivityBind",
    # M0 foundations
    "Provenance","DomainNode","Connection","Signal","Bundle","MethodInterface",
]

# Important to place after all data-model classes have been imported
profile(__name__)

# Note: 'fe' module is only available in zuspec.ir.core, not the old zuspec.dataclasses.ir
# from . import fe
