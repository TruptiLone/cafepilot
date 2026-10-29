"""Superset DAG: every dependency the agent's plans are allowed to contain.

Two kinds of edges between actions:
- data edges: A -> B when A produces something B consumes (computed automatically);
- safety edges: B.requires names A (stated explicitly, mandatory).

Data edges say what *may* feed what; safety edges say what *must* come first.
The validator checks a plan's ``$ref`` edges against ``allowed_edges`` and its
nodes against ``required_predecessors``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from graphlib import CycleError, TopologicalSorter

from backend.modules.agent.registry import REGISTRY, Action


@dataclass(frozen=True)
class SupersetDAG:
    data_edges: frozenset[tuple[str, str]]
    safety_edges: frozenset[tuple[str, str]]

    @property
    def allowed_edges(self) -> frozenset[tuple[str, str]]:
        return self.data_edges | self.safety_edges

    def required_predecessors(self, action_name: str) -> tuple[tuple[str, ...], ...]:
        return REGISTRY[action_name].requires

    def topological_order(self) -> list[str]:
        sorter: TopologicalSorter[str] = TopologicalSorter()
        for name in REGISTRY:
            sorter.add(name)
        for upstream, downstream in self.allowed_edges:
            sorter.add(downstream, upstream)
        return list(sorter.static_order())


def _data_edges(actions: dict[str, Action]) -> set[tuple[str, str]]:
    edges = set()
    for upstream in actions.values():
        for downstream in actions.values():
            if upstream.name != downstream.name and set(upstream.produces) & set(
                downstream.consumes
            ):
                edges.add((upstream.name, downstream.name))
    return edges


def _safety_edges(actions: dict[str, Action]) -> set[tuple[str, str]]:
    edges = set()
    for action in actions.values():
        for group in action.requires:
            for upstream in group:
                if upstream not in actions:
                    raise RuntimeError(f"{action.name} requires unknown action {upstream}")
                edges.add((upstream, action.name))
    return edges


def build_superset(actions: dict[str, Action] | None = None) -> SupersetDAG:
    actions = REGISTRY if actions is None else actions
    dag = SupersetDAG(
        data_edges=frozenset(_data_edges(actions)),
        safety_edges=frozenset(_safety_edges(actions)),
    )
    try:
        dag.topological_order()
    except CycleError as error:
        raise RuntimeError(f"superset DAG has a cycle: {error.args[1]}") from error
    return dag


@lru_cache
def get_superset() -> SupersetDAG:
    return build_superset()
