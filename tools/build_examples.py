"""Regenerate examples/ from scratch (base repos, fixed copies, patches, invalid variants).

The committed patches are the source of truth; this script documents how they were made
and lets you tweak a bug and re-derive gold.patch/test.patch consistently.
"""
import shutil, sys, textwrap
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from repoval.mktask import make_task

OUT = Path(__file__).resolve().parent.parent / "examples"
WORK = Path(__import__("tempfile").mkdtemp(prefix="repoval-build-"))
shutil.rmtree(WORK, ignore_errors=True); shutil.rmtree(OUT, ignore_errors=True)

def w(root, rel, text):
    p = root / rel; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip("\n"))

def clone(src, dst):
    shutil.copytree(src, dst)

def repo(name, files):
    base = WORK / name / "base"
    for rel, text in files.items():
        w(base, rel, text)
    w(base, "tests/__init__.py", "")
    # the committed repo snapshot (examples/repos/<name>) is the base tree
    shutil.copytree(base, OUT / "repos" / name)
    return base

# ------------------------------------------------------------------ slugify_lite
slug = repo("slugify_lite", {
 "slugify_lite/__init__.py": "from .slugify import slugify\n",
 "slugify_lite/slugify.py": '''
    """Turn arbitrary text into URL-safe slugs."""
    import re
    import unicodedata


    def slugify(text, sep="-", max_length=None):
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        text = re.sub(r"[^a-zA-Z0-9]+", sep, text.lower()).strip(sep)
        if max_length is not None:
            text = text[:max_length]
        return text
    ''',
 "tests/test_slugify.py": '''
    from slugify_lite import slugify


    def test_basic():
        assert slugify("Hello, World!") == "hello-world"


    def test_unicode_is_transliterated():
        assert slugify("Crème Brûlée") == "creme-brulee"


    def test_custom_separator():
        assert slugify("a b c", sep="_") == "a_b_c"


    def test_no_limit_keeps_everything():
        assert slugify("one two three four") == "one-two-three-four"
    ''',
})
fixed = WORK / "slugify_lite" / "fixed"; clone(slug, fixed)
w(fixed, "slugify_lite/slugify.py", '''
    """Turn arbitrary text into URL-safe slugs."""
    import re
    import unicodedata


    def slugify(text, sep="-", max_length=None):
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        text = re.sub(r"[^a-zA-Z0-9]+", sep, text.lower()).strip(sep)
        if max_length is not None and len(text) > max_length:
            head = text[:max_length]
            # Prefer cutting on a word boundary; only hard-cut a single over-long word.
            if text[max_length] != sep and sep in head:
                head = head[: head.rindex(sep)]
            text = head.strip(sep)
        return text
    ''')
with open(fixed / "tests/test_slugify.py", "a") as f:
    f.write(textwrap.dedent('''

    def test_truncates_on_word_boundary():
        assert slugify("hello world foo", max_length=7) == "hello"


    def test_exact_fit_keeps_whole_words():
        assert slugify("hello world foo", max_length=11) == "hello-world"


    def test_never_ends_with_separator():
        assert slugify("ab cd", max_length=3) == "ab"


    def test_single_long_word_is_hard_cut():
        assert slugify("supercalifragilistic", max_length=5) == "super"
    '''))
SLUG_TESTS = ["tests.test_slugify::test_basic", "tests.test_slugify::test_unicode_is_transliterated",
              "tests.test_slugify::test_custom_separator", "tests.test_slugify::test_no_limit_keeps_everything",
              # characterisation tests: these already pass today and must keep passing
              "tests.test_slugify::test_exact_fit_keeps_whole_words", "tests.test_slugify::test_single_long_word_is_hard_cut"]
SLUG_F2P = ["tests.test_slugify::test_truncates_on_word_boundary", "tests.test_slugify::test_never_ends_with_separator"]
SLUG_STATEMENT = '''
    slugify() with max_length cuts the slug at an arbitrary character, which produces ugly
    and sometimes malformed URLs:

        >>> slugify("hello world foo", max_length=7)
        'hello-w'
        >>> slugify("ab cd", max_length=3)
        'ab-'

    Truncated slugs should end on a whole word where possible and must never end with
    the separator. If the very first word is longer than max_length there is no word
    boundary to use, so cutting inside that word is acceptable.
    '''
make_task(slug, fixed, OUT/"tasks"/"slugify-max-length", "slugify-max-length", textwrap.dedent(SLUG_STATEMENT),
          SLUG_F2P, SLUG_TESTS, "../../repos/slugify_lite", {"difficulty": "easy", "area": "string handling"})

# ------------------------------------------------------------------ ledger
led = repo("ledger", {
 "ledger/__init__.py": "from .money import split_evenly\n",
 "ledger/money.py": '''
    """Money helpers. Amounts are integer cents to avoid float drift."""


    def split_evenly(total_cents, parts):
        """Split ``total_cents`` into ``parts`` shares."""
        if parts <= 0:
            raise ValueError("parts must be positive")
        share = round(total_cents / parts)
        return [share] * parts
    ''',
 "tests/test_money.py": '''
    import pytest

    from ledger import split_evenly


    def test_even_split():
        assert split_evenly(100, 4) == [25, 25, 25, 25]


    def test_single_part():
        assert split_evenly(7, 1) == [7]


    def test_rejects_zero_parts():
        with pytest.raises(ValueError):
            split_evenly(100, 0)
    ''',
})
fixed = WORK / "ledger" / "fixed"; clone(led, fixed)
w(fixed, "ledger/money.py", '''
    """Money helpers. Amounts are integer cents to avoid float drift."""


    def split_evenly(total_cents, parts):
        """Split ``total_cents`` into ``parts`` shares that always sum to the total."""
        if parts <= 0:
            raise ValueError("parts must be positive")
        base, extra = divmod(total_cents, parts)
        # The first ``extra`` shares absorb the leftover cents, one each.
        return [base + 1 if i < extra else base for i in range(parts)]
    ''')
with open(fixed / "tests/test_money.py", "a") as f:
    f.write(textwrap.dedent('''

    def test_remainder_goes_to_first_shares():
        assert split_evenly(100, 3) == [34, 33, 33]


    @pytest.mark.parametrize("total,parts", [(10, 3), (7, 2), (100, 3), (1, 3)])
    def test_shares_sum_to_total(total, parts):
        assert sum(split_evenly(total, parts)) == total
    '''))
LED_P2P = ["tests.test_money::test_even_split", "tests.test_money::test_single_part", "tests.test_money::test_rejects_zero_parts"]
LED_F2P = ["tests.test_money::test_remainder_goes_to_first_shares"] + [
    f"tests.test_money::test_shares_sum_to_total[{t}-{p}]" for t, p in [(10, 3), (7, 2), (100, 3), (1, 3)]]
LED_STATEMENT = '''
    Splitting a bill loses or invents money. split_evenly(100, 3) returns [33, 33, 33], so one
    cent has vanished from the ledger, and split_evenly(7, 2) returns [4, 4], which invents one.

    The shares must always add up to exactly the total. When the total does not divide evenly,
    the leftover cents should go to the first shares, one cent each, so the result is
    deterministic: split_evenly(100, 3) -> [34, 33, 33].
    '''
make_task(led, fixed, OUT/"tasks"/"ledger-split-cents", "ledger-split-cents", textwrap.dedent(LED_STATEMENT),
          LED_F2P, LED_P2P, "../../repos/ledger", {"difficulty": "easy", "area": "arithmetic"})

# ------------------------------------------------------------------ cron_lite
cron = repo("cron_lite", {
 "cron_lite/__init__.py": "from .fields import expand_field\n",
 "cron_lite/fields.py": '''
    """Expand a single cron field ("*/15", "1-5", "1,15,30") into explicit values."""


    def expand_field(expr, lo, hi):
        values = set()
        for part in expr.split(","):
            step = 1
            if "/" in part:
                part, step_text = part.split("/")
                step = int(step_text)
            if part == "*":
                start, end = lo, hi
            elif "-" in part:
                first, last = part.split("-")
                start, end = int(first), int(last)
            else:
                values.add(int(part))
                continue
            values.update(range(start, end, step))
        return sorted(values)
    ''',
 "tests/test_fields.py": '''
    from cron_lite import expand_field


    def test_single_value():
        assert expand_field("5", 0, 59) == [5]


    def test_list():
        assert expand_field("1,15,30", 0, 59) == [1, 15, 30]


    def test_star_with_step():
        assert expand_field("*/15", 0, 59) == [0, 15, 30, 45]
    ''',
})
fixed = WORK / "cron_lite" / "fixed"; clone(cron, fixed)
w(fixed, "cron_lite/fields.py", '''
    """Expand a single cron field ("*/15", "1-5", "1,15,30") into explicit values."""


    def expand_field(expr, lo, hi):
        values = set()
        for part in expr.split(","):
            step = 1
            if "/" in part:
                part, step_text = part.split("/")
                step = int(step_text)
            if part == "*":
                start, end = lo, hi
            elif "-" in part:
                first, last = part.split("-")
                start, end = int(first), int(last)
            else:
                values.add(int(part))
                continue
            values.update(range(start, end + 1, step))  # cron ranges are inclusive on both ends
        return sorted(values)
    ''')
with open(fixed / "tests/test_fields.py", "a") as f:
    f.write(textwrap.dedent('''

    def test_range_is_inclusive():
        assert expand_field("1-5", 0, 59) == [1, 2, 3, 4, 5]


    def test_star_includes_upper_bound():
        assert expand_field("*", 0, 5) == [0, 1, 2, 3, 4, 5]


    def test_range_with_step():
        assert expand_field("1-10/3", 0, 59) == [1, 4, 7, 10]
    '''))
CRON_P2P = ["tests.test_fields::test_single_value", "tests.test_fields::test_list", "tests.test_fields::test_star_with_step"]
CRON_F2P = ["tests.test_fields::test_range_is_inclusive", "tests.test_fields::test_star_includes_upper_bound",
            "tests.test_fields::test_range_with_step"]
CRON_STATEMENT = '''
    Ranges in our cron field parser stop one short. "0 9 1-5 * *" is supposed to run on days
    1 through 5, but expand_field("1-5", 0, 59) returns [1, 2, 3, 4], and "*" over minutes
    0..5 skips the last value. Same for stepped ranges: expand_field("1-10/3", 0, 59) is missing 10.

    In cron both ends of a range are inclusive.
    '''
make_task(cron, fixed, OUT/"tasks"/"cron-inclusive-range", "cron-inclusive-range", textwrap.dedent(CRON_STATEMENT),
          CRON_F2P, CRON_P2P, "../../repos/cron_lite", {"difficulty": "easy", "area": "parsing"})

# ------------------------------------------------------------------ ttl_cache
ttl = repo("ttl_cache", {
 "ttl_cache/__init__.py": "from .cache import TTLCache\n",
 "ttl_cache/cache.py": '''
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
    ''',
 "tests/conftest.py": '''
    import pytest


    class FakeClock:
        def __init__(self):
            self.now = 1000.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds


    @pytest.fixture
    def clock():
        return FakeClock()
    ''',
 "tests/test_cache.py": '''
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
    ''',
})
fixed = WORK / "ttl_cache" / "fixed"; clone(ttl, fixed)
w(fixed, "ttl_cache/cache.py", '''
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
            if self._clock() >= expires_at:
                del self._data[key]  # expired entries must not linger and count toward len()
                return default
            return value

        def __len__(self):
            return len(self._data)
    ''')
with open(fixed / "tests/test_cache.py", "a") as f:
    f.write(textwrap.dedent('''

    def test_expires_exactly_at_ttl(clock):
        cache = TTLCache(ttl=10, clock=clock)
        cache.set("a", 1)
        clock.advance(10)
        assert cache.get("a") is None


    def test_expired_entry_is_removed(clock):
        cache = TTLCache(ttl=10, clock=clock)
        cache.set("a", 1)
        clock.advance(11)
        cache.get("a")
        assert len(cache) == 0
    '''))
TTL_P2P = ["tests.test_cache::test_hit_before_expiry", "tests.test_cache::test_miss_returns_default", "tests.test_cache::test_lru_eviction"]
TTL_F2P = ["tests.test_cache::test_expires_exactly_at_ttl", "tests.test_cache::test_expired_entry_is_removed"]
TTL_STATEMENT = '''
    TTLCache serves stale data and leaks memory.

    1. An entry with ttl=10 written at t=0 is still returned when read at exactly t=10. A TTL of
       10 seconds means the value is gone at 10 seconds, not 10 seconds and a tick.
    2. Once an entry has expired and been read, it stays in the internal store, so len(cache) keeps
       counting dead entries and they only disappear when LRU pressure pushes them out.
    '''
make_task(ttl, fixed, OUT/"tasks"/"ttl-expiry-boundary", "ttl-expiry-boundary", textwrap.dedent(TTL_STATEMENT),
          TTL_F2P, TTL_P2P, "../../repos/ttl_cache", {"difficulty": "medium", "area": "caching"})

# =================================================================== INVALID tasks
INV = OUT / "tasks_invalid"
import json

def clone_task(src, dst_name, mutate):
    dst = INV / dst_name
    shutil.copytree(OUT / "tasks" / src, dst)
    spec = json.loads((dst / "task.json").read_text())
    spec["instance_id"] = dst_name
    spec["repo"] = spec["repo"].replace("../../repos", "../../repos")  # same depth: tasks_invalid/<x>
    mutate(dst, spec)
    (dst / "task.json").write_text(json.dumps(spec, indent=2) + "\n")

# 1. vacuous F2P: declares a test that already passes without the fix
def vacuous(dst, spec):
    t = (dst / "test.patch").read_text()
    t += textwrap.dedent('''\
        diff --git a/tests/test_extra.py b/tests/test_extra.py
        new file mode 100644
        --- /dev/null
        +++ b/tests/test_extra.py
        @@ -0,0 +1,5 @@
        +from slugify_lite import slugify
        +
        +
        +def test_lowercases():
        +    assert slugify("ABC") == "abc"
        ''')
    (dst / "test.patch").write_text(t)
    spec["fail_to_pass"].append("tests.test_extra::test_lowercases")
clone_task("slugify-max-length", "bad-vacuous-f2p", vacuous)

# 2. flaky: a P2P test that depends on the per-process hash seed
def flaky(dst, spec):
    t = (dst / "test.patch").read_text()
    t += textwrap.dedent('''\
        diff --git a/tests/test_ordering.py b/tests/test_ordering.py
        new file mode 100644
        --- /dev/null
        +++ b/tests/test_ordering.py
        @@ -0,0 +1,7 @@
        +import sys
        +
        +
        +def test_report_ordering_matches_author_machine():
        +    # Written on a machine where PYTHONHASHSEED=0 happened to be set, so
        +    # iteration order over string sets "worked". Passes only without randomisation.
        +    assert sys.flags.hash_randomization == 0
        ''')
    (dst / "test.patch").write_text(t)
    spec["pass_to_pass"].append("tests.test_ordering::test_report_ordering_matches_author_machine")
clone_task("slugify-max-length", "bad-flaky-p2p", flaky)

# 3. leaky statement: the issue text pastes the fix
def leaky(dst, spec):
    spec["problem_statement"] += ("\nSuggested change: replace the split with\n\n"
                                  "    base, extra = divmod(total_cents, parts)\n")
clone_task("ledger-split-cents", "warn-leaky-statement", leaky)

# 4. gold patch edits a test (could weaken the grader)
def gold_edits_tests(dst, spec):
    g = (dst / "gold.patch").read_text()
    g += textwrap.dedent('''\
        diff --git a/tests/test_money.py b/tests/test_money.py
        --- a/tests/test_money.py
        +++ b/tests/test_money.py
        @@ -6,7 +6,7 @@
 
 
         def test_even_split():
        -    assert split_evenly(100, 4) == [25, 25, 25, 25]
        +    assert sum(split_evenly(100, 4)) == 100
 
 
         def test_single_part():
        ''').replace("\n \n \n", "\n\n\n")
    (dst / "gold.patch").write_text(g)
clone_task("ledger-split-cents", "bad-gold-edits-tests", gold_edits_tests)

# 5. typo in a declared test id
def typo(dst, spec):
    spec["fail_to_pass"][0] = "tests.test_fields::test_range_is_inclusve"
clone_task("cron-inclusive-range", "bad-missing-test-id", typo)

# 6. gold patch is incomplete: one F2P test still fails after it
def half_fix(dst, spec):
    g = (dst / "gold.patch").read_text().replace("range(start, end + 1, step)", "range(start, end + 1, step) if part != '*' else range(start, end, step)")
    (dst / "gold.patch").write_text(g)
clone_task("cron-inclusive-range", "bad-incomplete-gold", half_fix)
print("built")
