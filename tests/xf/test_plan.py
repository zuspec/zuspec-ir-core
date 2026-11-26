"""Planner v0: ordering of ``schedule`` members by the flow objects they
exchange (``zuspec.ir.core.xf.plan``)."""
import pytest

from zuspec.ir.core.xf.plan import (
    MAX_ORDERS, CycleError, Footprint, ParallelError, linear_extensions, plan_schedule)


def test_independent_members_run_alone():
    plan = plan_schedule([Footprint(), Footprint(buf_out={"B"})])
    assert plan.groups == [[0], [1]]
    assert not plan.interacts
    assert plan.edges == set()


def test_buffer_producer_runs_before_consumer():
    plan = plan_schedule([Footprint(buf_in={"B"}), Footprint(buf_out={"B"})])
    assert plan.edges == {(1, 0)}
    assert plan.groups == [[0, 1]]
    assert plan.orders == [[[1, 0]]]


def test_two_producers_run_before_the_consumer_in_either_order():
    plan = plan_schedule([Footprint(buf_out={"B"}), Footprint(buf_out={"B"}),
                          Footprint(buf_in={"B"})])
    assert plan.groups == [[0, 1, 2]]
    assert plan.orders == [[[0, 1, 2], [1, 0, 2]]]


def test_state_writers_take_turns_in_any_order():
    plan = plan_schedule([Footprint(state_out={"S"}), Footprint(state_in={"S"}),
                          Footprint()])
    assert plan.edges == set()
    assert plan.groups == [[0, 1], [2]]
    assert plan.orders[0] == [[0, 1], [1, 0]]
    assert plan.interacts


def test_state_readers_alone_do_not_interact():
    plan = plan_schedule([Footprint(state_in={"S"}), Footprint(state_in={"S"})])
    assert not plan.interacts


def test_resource_lockers_take_turns_in_any_order():
    plan = plan_schedule([Footprint(res_lock={"R"}), Footprint(res_share={"R"}),
                          Footprint(res_lock={"Q"})])
    assert plan.edges == set()
    assert plan.groups == [[0, 1], [2]]
    assert plan.orders[0] == [[0, 1], [1, 0]]


def test_resource_sharers_do_not_interact():
    plan = plan_schedule([Footprint(res_share={"R"}), Footprint(res_share={"R"})])
    assert not plan.interacts


def test_cycle_is_refused():
    with pytest.raises(CycleError) as e:
        plan_schedule([Footprint(buf_out={"A"}, buf_in={"B"}),
                       Footprint(buf_out={"B"}, buf_in={"A"})])
    assert e.value.cycle[0] == e.value.cycle[-1]


def test_linear_extensions_capped_and_stable():
    group = list(range(5))  # 120 orders, no edges
    a = linear_extensions(group, set())
    assert len(a) == MAX_ORDERS
    assert a == linear_extensions(group, set())
    assert a == sorted(a)
    assert len(linear_extensions(group, set(), cap=200)) == 120


def test_linear_extensions_keep_edges():
    orders = linear_extensions([0, 1, 2], {(2, 0)})
    assert orders == [[1, 2, 0], [2, 0, 1], [2, 1, 0]]


def test_sequence_constraint_orders_independent_members():
    plan = plan_schedule([Footprint(), Footprint(), Footprint()], before=[(2, 0)])
    assert plan.groups == [[0, 2], [1]]
    assert plan.orders[0] == [[2, 0]]


def test_sequence_constraint_against_flow_is_a_cycle():
    with pytest.raises(CycleError):
        plan_schedule([Footprint(buf_out={"B"}), Footprint(buf_in={"B"})],
                      before=[(1, 0)])


def test_parallel_constraint_over_independent_members_holds():
    plan = plan_schedule([Footprint(), Footprint()], together=[[0, 1]])
    assert not plan.interacts


def test_parallel_constraint_over_tied_members_refused():
    with pytest.raises(ParallelError) as e:
        plan_schedule([Footprint(state_out={"S"}), Footprint(state_out={"S"})],
                      together=[[0, 1]])
    assert e.value.tied


def test_parallel_constraint_with_grouped_member_refused():
    fps = [Footprint(buf_out={"B"}), Footprint(buf_in={"B"}), Footprint()]
    with pytest.raises(ParallelError) as e:
        plan_schedule(fps, together=[[1, 2]])
    assert not e.value.tied


def test_stream_partners_run_alone_together():
    """A stream output and input of one type start together (M8b4)."""
    plan = plan_schedule([Footprint(stream_in={"S"}), Footprint(stream_out={"S"})])
    assert plan.groups == [[0], [1]] and not plan.edges


def test_stream_partners_tied_otherwise_refused():
    with pytest.raises(ParallelError) as e:
        plan_schedule([Footprint(stream_out={"S"}, buf_out={"B"}),
                       Footprint(stream_in={"S"}, buf_in={"B"})])
    assert e.value.tied
