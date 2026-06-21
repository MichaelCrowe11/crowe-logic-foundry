class LRUCache:
    """Fixed-capacity cache evicting the least-recently-used key."""

    def __init__(self, capacity):
        self.capacity = capacity
        self.store = {}

    def get(self, key):
        return self.store.get(key, -1)

    def put(self, key, value):
        # BUG: never evicts; ignores recency.
        self.store[key] = value
