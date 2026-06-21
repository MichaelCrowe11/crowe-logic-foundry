from dijkstra import shortest_path


def test_picks_cheapest_path():
    graph = {
        "a": [("b", 1), ("c", 4)],
        "b": [("c", 1), ("d", 5)],
        "c": [("d", 1)],
        "d": [],
    }
    assert shortest_path(graph, "a", "d") == 3  # a->b->c->d


def test_unreachable():
    graph = {"a": [("b", 1)], "b": [], "c": []}
    assert shortest_path(graph, "a", "c") == float("inf")
