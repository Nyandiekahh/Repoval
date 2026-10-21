import pytest

from ledger import split_evenly


def test_even_split():
    assert split_evenly(100, 4) == [25, 25, 25, 25]


def test_single_part():
    assert split_evenly(7, 1) == [7]


def test_rejects_zero_parts():
    with pytest.raises(ValueError):
        split_evenly(100, 0)
