from __future__ import annotations
import logging
from typing import ClassVar, Dict, Type, Self, TYPE_CHECKING

from .base import Base

class Visitor:
    _type_impl_m : Dict[Type[Visitor],Type[Visitor]] = {}
    _log : ClassVar = logging.getLogger("zuspec.ir.Visitor")

    def __new__(cls, pmod) -> Visitor:
        if pmod is None:
            return super().__new__(cls)
        elif cls in Visitor._type_impl_m.keys():
            return Visitor._type_impl_m[cls].__new__(cls, pmod)
        else:
            # Build out the implementation
            # Need a visitor for each Base-derived 
            # object
            #
            # If this class doesn't provide one, add an 
            # implementation that redirects to visitBase
            #
            # Create a class that implements any 
            from .profile_rgy import ProfileRgy
            fields = {}
            profile = ProfileRgy.get_profile(pmod)
            for t in profile.types:
                if hasattr(cls, "visit%s" % t.__name__):
                    print("Class has %s" % t.__name__)
                    fields["visit%s" % t.__name__] = getattr(cls, "visit%s" % t.__name__)
                else:
                    print("Must define %s" % t.__name__)
                    fields["visit%s" % t.__name__] = lambda self,o,cls=t: Visitor.visitDefault(self, o, t)

            fields["__new__"] = lambda cls,pmod=None: super().__new__(cls)

            impl : Type[Visitor] = type(
                cls.__qualname__, 
                (cls,), 
                fields)

            print("impl: %s" % str(impl))
            print("cls: %s" % str(cls))
            Visitor._type_impl_m[cls] = impl

            return impl(None)
        
    @staticmethod
    def visitDefault(self : Visitor, obj : Base, cls : Type):
        self._log.debug("--> visitDefault virt=%s" % cls.__name__)
        if type(self) == cls:
            obj.accept(self)
        else:
            obj.visitDefault(self, cls)
        self._log.debug("<-- visitDefault virt=%s" % cls.__name__)

        
    # def __init__(self, pmod):
    #     pass

    def visitBase(self, o : Base):
        o.visitDefault(self)
        pass

    # ------------------------------------------------------------------
    # Activity IR visitor methods (default: visit children via accept)
    # ------------------------------------------------------------------

    def visitJoinSpec(self, o):
        pass

    def visitActivityStmt(self, o):
        o.visitDefault(self, type(o))

    def visitActivitySequenceBlock(self, o):
        for stmt in o.stmts:
            stmt.accept(self)

    def visitActivityParallel(self, o):
        for stmt in o.stmts:
            stmt.accept(self)

    def visitActivitySchedule(self, o):
        for stmt in o.stmts:
            stmt.accept(self)

    def visitActivityAtomic(self, o):
        for stmt in o.stmts:
            stmt.accept(self)

    def visitActivityTraversal(self, o):
        pass

    def visitActivityAnonTraversal(self, o):
        pass

    def visitActivitySuper(self, o):
        pass

    def visitActivityRepeat(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitActivityDoWhile(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitActivityWhileDo(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitActivityForeach(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitActivityReplicate(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitSelectBranch(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitActivitySelect(self, o):
        for branch in o.branches:
            branch.accept(self)

    def visitActivityIfElse(self, o):
        for stmt in o.if_body:
            stmt.accept(self)
        for stmt in o.else_body:
            stmt.accept(self)

    def visitMatchCase(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitActivityMatch(self, o):
        for case in o.cases:
            case.accept(self)

    def visitActivityConstraint(self, o):
        pass

    def visitActivityBind(self, o):
        pass

    # ------------------------------------------------------------------
    # Interface-Protocol IR visitor stubs
    # ------------------------------------------------------------------

    def visitSpawnStmt(self, o):
        """Visit a SpawnStmt node (default: no-op)."""
        pass

    def visitSelectStmt(self, o):
        """Visit a SelectStmt node (default: no-op)."""
        pass

    def visitCompletionSetStmt(self, o):
        """Visit a CompletionSetStmt node (default: no-op)."""
        pass

    def visitQueuePutStmt(self, o):
        """Visit a QueuePutStmt node (default: no-op)."""
        pass

    def visitCompletionAwaitExpr(self, o):
        """Visit a CompletionAwaitExpr node (default: no-op)."""
        pass

    def visitQueueGetExpr(self, o):
        """Visit a QueueGetExpr node (default: no-op)."""
        pass

    # ------------------------------------------------------------------
    # Scenario Runtime IR (Layer-1 `scenario` dialect) visitor defaults.
    # Defaults visit structured children via accept(); leaf ops are no-ops.
    # ------------------------------------------------------------------

    def visitScStmt(self, o):
        o.visitDefault(self, type(o))

    def visitScCoroutine(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitScExecBlock(self, o):
        pass

    def visitScSeq(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitScPar(self, o):
        for stmt in o.branches:
            stmt.accept(self)

    def visitScSelectBranch(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitScSelect(self, o):
        for branch in o.branches:
            branch.accept(self)

    def visitScLoop(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitScAtomic(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitScIf(self, o):
        for stmt in o.then_body:
            stmt.accept(self)
        for stmt in o.else_body:
            stmt.accept(self)

    def visitScMatchCase(self, o):
        for stmt in o.body:
            stmt.accept(self)

    def visitScMatch(self, o):
        for case in o.cases:
            case.accept(self)

    def visitScInvoke(self, o):
        pass

    def visitScSpawn(self, o):
        pass

    def visitScJoin(self, o):
        pass

    def visitScWait(self, o):
        pass

    def visitScImport(self, o):
        pass

    def visitScImportDecl(self, o):
        pass

    def visitScSolveVar(self, o):
        pass

    def visitScSolveProblem(self, o):
        for var in o.vars:
            var.accept(self)

    def visitScActionInst(self, o):
        pass

    def visitScComponentInst(self, o):
        for child in o.children:
            child.accept(self)

    def visitScenarioModule(self, o):
        for coro in o.coroutines.values():
            coro.accept(self)



