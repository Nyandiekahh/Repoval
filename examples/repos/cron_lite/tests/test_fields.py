from cron_lite import expand_field


def test_single_value():
    assert expand_field("5", 0, 59) == [5]


def test_list():
    assert expand_field("1,15,30", 0, 59) == [1, 15, 30]


def test_star_with_step():
    assert expand_field("*/15", 0, 59) == [0, 15, 30, 45]
