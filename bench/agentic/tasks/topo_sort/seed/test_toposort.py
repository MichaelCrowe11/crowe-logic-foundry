import pytest

from toposort import topo_sort


def test_orders_dependencies_first():
    graph = {"a": ["b"], "b": ["c"], "c": []}
    order = topo_sort(graph)
    assert order.index("c") < order.index("b") < order.index("a")


def test_detects_cycle():
    graph = {"a": ["b"], "b": ["a"]}
    with pytest.raises(ValueError):
        topo_sort(graph)
