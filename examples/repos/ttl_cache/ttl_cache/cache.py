"""A small LRU cache whose entries also expire after a TTL."""
import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, ttl, maxsize=128, clock=time.monotonic):
        self.ttl, self.maxsize, self._clock = ttl, maxsize, clock
        self._data = OrderedDict()

    def set(self, key, value):
        self._data[key] = (value, self._clock() + self.ttl)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def get(self, key, default=None):
        item = self._data.get(key)
        if item is None:
            return default
        value, expires_at = item
        if self._clock() > expires_at:
            return default
        return value

    def __len__(self):
        return len(self._data)
