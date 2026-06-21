from lru import LRUCache


def test_evicts_least_recently_used():
    c = LRUCache(2)
    c.put(1, 1)
    c.put(2, 2)
    assert c.get(1) == 1      # 1 is now most-recently-used
    c.put(3, 3)               # evicts key 2
    assert c.get(2) == -1
    assert c.get(3) == 3


def test_update_existing_does_not_grow():
    c = LRUCache(2)
    c.put(1, 1)
    c.put(1, 10)
    c.put(2, 2)
    assert c.get(1) == 10
    assert c.get(2) == 2
