"""Walking a lilv collection without lilv installed.

lilv's `Collection.__iter__` returns an `Iter` that has `__next__` and no
`__iter__`, and on Python 3.13 a comprehension over it raises "'Iter' object is
not iterable". The fakes below have lilv's shape, so a walk that needs the
iterator to be iterable fails here, on any Python, with no lilv.
"""
import pytest

from sushi_rig.probe import _each, _port_scale_points, _unit_symbol


class Iter:
    """Like lilv's: `__next__`, and deliberately no `__iter__`."""

    def __init__(self, items):
        self._items = list(items)

    def __next__(self):
        if not self._items:
            raise StopIteration
        return self._items.pop(0)


class Collection:
    def __init__(self, *items):
        self._items = items

    def __iter__(self):
        return Iter(self._items)


def test_the_fake_is_as_hostile_as_lilv():
    with pytest.raises(TypeError):
        list(iter(Iter([1])))


def test_each_walks_a_collection_whose_iterator_is_not_iterable():
    assert list(_each(Collection(1, 2, 3))) == [1, 2, 3]
    assert [str(n) for n in _each(Collection("a", "b"))] == ["a", "b"]
    assert sorted(_each(Collection(3, 1, 2))) == [1, 2, 3]


def test_each_of_nothing_and_of_an_empty_collection_is_empty():
    assert list(_each(None)) == []
    assert list(_each(Collection())) == []


def test_each_still_walks_a_plain_list():
    assert list(_each([1, 2])) == [1, 2]


class FakeWorld:
    def new_uri(self, uri):
        return uri

    def get(self, node, predicate, _):
        return f"sym-{node}"


class FakePort:
    def get_value(self, predicate):
        return Collection("dB", "Hz")


def test_the_unit_symbol_is_read_through_such_a_collection():
    assert _unit_symbol(FakeWorld(), FakePort()) == "sym-Hz"


class FakePoint:
    def __init__(self, value, label):
        self._value, self._label = value, label

    def get_value(self):
        return self._value

    def get_label(self):
        return self._label


def test_scale_points_are_read_through_such_a_collection():
    class Port:
        def get_scale_points(self):
            return Collection(FakePoint("1", "On"), FakePoint("0", "Off"))

    assert [p["label"] for p in _port_scale_points(Port())] == ["Off", "On"]
