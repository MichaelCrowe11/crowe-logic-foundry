import heapq


def shortest_path(graph, start, end):
    """Return the shortest path cost from start to end (non-negative weights).

    graph maps node -> list of (neighbor, weight). Return float('inf') if
    unreachable.
    """
    # BUG: uses a plain queue and returns the FIRST path found, not the cheapest.
    queue = [(start, 0)]
    while queue:
        node, cost = queue.pop(0)
        if node == end:
            return cost
        for nbr, w in graph.get(node, []):
            queue.append((nbr, cost + w))
    return float("inf")
