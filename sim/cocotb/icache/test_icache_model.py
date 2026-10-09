"""Checks of the reference model alone; no simulator, so they run instantly."""

from icache_model import ICacheModel


def test_address_split():
    m = ICacheModel(tag_bits=5)
    assert m.split(0x000) == (0, 0)
    assert m.split(0x01C) == (7, 0)
    assert m.split(0x020) == (0, 1)
    assert m.split(0x3FC) == (7, 31)
    assert m.split(0x400) == (0, 0)  # bits above the tag are ignored


def test_four_bit_tag():
    m = ICacheModel(tag_bits=4)
    assert m.split(0x1FC) == (7, 15)
    assert m.split(0x200) == (0, 0)


def test_lru():
    m = ICacheModel(tag_bits=5)
    a, b, c = 0x020, 0x040, 0x060  # same set, three tags
    assert [m.access(x) for x in (a, b, a, c, a, b)] == [False, False, True, False, True, False]


def test_flush():
    m = ICacheModel(tag_bits=5)
    m.access(0x020)
    m.flush()
    assert not m.is_hit(0x020)
