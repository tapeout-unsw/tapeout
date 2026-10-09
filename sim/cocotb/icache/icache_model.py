"""Reference model of rtl/cache/icache.sv, written from the behaviour in the
header of that file. Plain Python, no simulator: it predicts whether each fetch
hits, which is all the core can observe besides the data.

  2-way set associative, 8 sets, one 32-bit word per line
  index = addr[4:2], tag = addr[tag_bits+4:5]
  LRU replacement: a hit or fill makes that line the most recently used
  flush and reset invalidate every line

The spec gives tag_bits = RAW-5 (6 bits for the 2 KiB RAM); test_icache.py
passes it in.
"""

SETS = 8
WAYS = 2


class ICacheModel:
    def __init__(self, tag_bits):
        self.tag_bits = tag_bits
        self.flush()

    def split(self, addr):
        """Return (index, tag) for a byte address."""
        index = (addr >> 2) % SETS
        tag = (addr >> 5) & ((1 << self.tag_bits) - 1)
        return index, tag

    def is_hit(self, addr):
        index, tag = self.split(addr)
        return tag in self.sets[index]

    def access(self, addr):
        """Fetch addr: update the replacement state and return True on a hit."""
        index, tag = self.split(addr)
        ways = self.sets[index]
        hit = tag in ways
        if hit:
            ways.remove(tag)
        ways.insert(0, tag)
        del ways[WAYS:]
        return hit

    def flush(self):
        # Each set lists its valid tags, most recently used first.
        self.sets = [[] for _ in range(SETS)]
