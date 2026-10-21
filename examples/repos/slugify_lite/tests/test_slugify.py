from slugify_lite import slugify


def test_basic():
    assert slugify("Hello, World!") == "hello-world"


def test_unicode_is_transliterated():
    assert slugify("Crème Brûlée") == "creme-brulee"


def test_custom_separator():
    assert slugify("a b c", sep="_") == "a_b_c"


def test_no_limit_keeps_everything():
    assert slugify("one two three four") == "one-two-three-four"
