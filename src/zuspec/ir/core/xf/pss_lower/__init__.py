"""PSS → Scenario lowering (Layer 0 → Layer 1).

``PSSToScenarioPass`` is the umbrella transform that lowers PSS-semantic Layer-0
IR to the target-neutral ``scenario`` dialect.  Per the impl plan it is an
ordered sequence of sub-passes::

    TraversalResolve → ScheduleNormalize → ConstraintCollect → FlowBind → LifecycleNormalize

Phase 1 implements the atomic-action slice: ``TraversalResolve`` (action-type
resolution) + ``LifecycleNormalize`` for atomic actions with an exec body and no
constraints (constraints are carried on the coroutine as ``pending_constraints``
until Phase 3 wires up ``ConstraintCollect``).  Compound actions (those with an
``activity_ir``) are recorded in ``ScenarioModule.deferred_actions`` for Phase 4.
"""
from .lower import PSSToScenarioPass

__all__ = ["PSSToScenarioPass"]
