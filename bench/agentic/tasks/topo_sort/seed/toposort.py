def topo_sort(graph):
    """Return a topological order of nodes; raise ValueError on a cycle.

    graph maps node -> list of nodes it depends on (must come before it).
    """
    # BUG: returns nodes in arbitrary order; no ordering, no cycle detection.
    return list(graph.keys())
