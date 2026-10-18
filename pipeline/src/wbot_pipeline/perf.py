"""The `perf` block of the JSON contract: counts in both on-time windows, nearest-rank
percentiles and a 1-minute histogram, computed in DuckDB for any grouping.

`reference()` computes the same block in plain Python; the tests check one against the other.
"""

import duckdb

from . import rules
from .config import ALT, HEADLINE, HIST_BUCKETS, HIST_START_MIN

COUNTS = ("n", "early", "on_time", "late", "early_alt", "on_time_alt", "late_alt")
PERCENTILES = (10, 50, 90)


def rank(pct: int, n: int) -> int:
    """1-based nearest rank: ceil(pct / 100 * n), in integers so no rounding creeps in."""
    return max(1, (pct * n + 99) // 100)


def empty(hist: bool = False, percentiles: bool = True) -> dict:
    out = dict.fromkeys(COUNTS, 0)
    if percentiles:
        out.update(p10=None, p50=None, p90=None)
    if hist:
        out["hist"] = {"start_min": HIST_START_MIN, "under": 0, "counts": [0] * HIST_BUCKETS, "over": 0}
    return out


def reference(delays: list[int], hist: bool = True, percentiles: bool = True) -> dict:
    delays = sorted(delays)
    out = {"n": len(delays)}
    for suffix, (lo, hi) in (("", HEADLINE), ("_alt", ALT)):
        out["early" + suffix] = sum(1 for v in delays if v < lo)
        out["on_time" + suffix] = sum(1 for v in delays if lo <= v <= hi)
        out["late" + suffix] = sum(1 for v in delays if v > hi)
    if percentiles:
        for p in PERCENTILES:
            out[f"p{p}"] = delays[rank(p, len(delays)) - 1] if delays else None
    if hist:
        counts, under, over = [0] * HIST_BUCKETS, 0, 0
        for v in delays:
            b = v // 60 - HIST_START_MIN
            if b < 0:
                under += 1
            elif b >= HIST_BUCKETS:
                over += 1
            else:
                counts[b] += 1
        out["hist"] = {"start_min": HIST_START_MIN, "under": under, "counts": counts, "over": over}
    return out


class PerfQuery:
    """Perf blocks from a table with a `delay` column (whole seconds). `extra` adds named
    counts to every block: {name: condition on delay}."""

    def __init__(self, con: duckdb.DuckDBPyConnection, table: str = "d", extra: dict[str, str] | None = None):
        self.con, self.table = con, table
        self.extra = extra or {}

    def get(self, keys: list[str], where: str = "TRUE", hist: bool = False,
            percentiles: bool = True) -> dict[tuple, dict]:
        k = ", ".join(keys)
        sel = k + ", " if keys else ""
        group = f"GROUP BY {k}" if keys else ""
        conditions = {**rules.window_counts("delay"), **self.extra}
        names = ["n", *conditions]
        filters = "".join(f", count(*) FILTER (WHERE {c})" for c in conditions.values())
        rows = self.con.execute(f"""
            SELECT {sel} count(*){filters}
            FROM {self.table} WHERE {where} {group}
        """).fetchall()
        n_keys = len(keys)
        out = {}
        for r in rows:
            if r[n_keys] == 0:
                continue
            out[tuple(r[:n_keys])] = dict(zip(names, (int(x) for x in r[n_keys:])))
        if percentiles:
            part = f"PARTITION BY {k}" if keys else ""
            picks = ", ".join(f"max(delay) FILTER (WHERE rn = greatest(1, ({p} * cnt + 99) // 100))" for p in PERCENTILES)
            for r in self.con.execute(f"""
                SELECT {sel} {picks} FROM (
                    SELECT {sel} delay, row_number() OVER ({part} ORDER BY delay) AS rn,
                           count(*) OVER ({part}) AS cnt
                    FROM {self.table} WHERE {where}
                ) {group}
            """).fetchall():
                key = tuple(r[:n_keys])
                if key in out:
                    out[key].update(zip(("p10", "p50", "p90"), (int(x) for x in r[n_keys:])))
        if hist:
            for v in out.values():
                v["hist"] = {"start_min": HIST_START_MIN, "under": 0, "counts": [0] * HIST_BUCKETS, "over": 0}
            for r in self.con.execute(f"""
                SELECT {sel} least(greatest(CAST(floor(delay / 60.0) AS INTEGER) - ({HIST_START_MIN}), -1),
                                   {HIST_BUCKETS}) AS b, count(*)
                FROM {self.table} WHERE {where} GROUP BY {sel} b
            """).fetchall():
                key, b, c = tuple(r[:n_keys]), r[n_keys], int(r[n_keys + 1])
                h = out[key]["hist"]
                if b < 0:
                    h["under"] += c
                elif b >= HIST_BUCKETS:
                    h["over"] += c
                else:
                    h["counts"][b] += c
        return out
