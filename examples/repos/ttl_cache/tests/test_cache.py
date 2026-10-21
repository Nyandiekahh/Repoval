from ttl_cache import TTLCache


def test_hit_before_expiry(clock):
    cache = TTLCache(ttl=10, clock=clock)
    cache.set("a", 1)
    clock.advance(9)
    assert cache.get("a") == 1


def test_miss_returns_default(clock):
    assert TTLCache(ttl=10, clock=clock).get("nope", "dflt") == "dflt"


def test_lru_eviction(clock):
    cache = TTLCache(ttl=10, maxsize=2, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    assert cache.get("a") is None
    assert cache.get("c") == 3
