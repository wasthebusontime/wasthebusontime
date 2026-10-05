import random

import duckdb
import pyarrow as pa
import pytest

from wbot_pipeline.perf import PerfQuery, empty, rank, reference


def query(delays_by_group):
    con = duckdb.connect()
    table = pa.table({"g": [g for g, ds in delays_by_group.items() for _ in ds],
                      "delay": [d for ds in delays_by_group.values() for d in ds]})
    con.register("t", table)
    return PerfQuery(con, "t")


def test_window_boundaries():
    p = reference([-61, -60, -1, 0, 300, 301], hist=False, percentiles=False)
    assert (p["early"], p["on_time"], p["late"]) == (3, 2, 1)
    assert (p["early_alt"], p["on_time_alt"], p["late_alt"]) == (1, 4, 1)


def test_nearest_rank():
    assert [rank(10, n) for n in (1, 9, 10, 11, 30)] == [1, 1, 1, 2, 3]   # no floating-point surprise at 30
    assert reference(list(range(1, 11)), hist=False)["p50"] == 5
    assert reference([], hist=False)["p10"] is None


def test_histogram_buckets():
    h = reference([-601, -600, -1, 0, 59, 60, 1199, 1200])["hist"]
    assert h["under"] == 1 and h["over"] == 1
    assert h["counts"][0] == 1      # -600: minute -10
    assert h["counts"][9] == 1      # -1: minute -1
    assert h["counts"][10] == 2     # 0 and 59: minute 0
    assert h["counts"][29] == 1     # 1199: minute 19


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_sql_matches_reference(seed):
    rng = random.Random(seed)
    groups = {f"g{i}": [rng.randint(-900, 1500) for _ in range(rng.randint(1, 200))] for i in range(5)}
    got = query(groups).get(["g"], hist=True)
    for g, ds in groups.items():
        assert got[(g,)] == reference(ds)
    total = query(groups).get([], hist=True)[()]
    assert total == reference([d for ds in groups.values() for d in ds])


def test_empty_block_shape():
    assert list(empty(hist=True)) == list(reference([1]))
    assert list(empty(percentiles=False)) == list(reference([1], hist=False, percentiles=False))
