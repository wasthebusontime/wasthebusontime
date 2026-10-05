"""DuckDB connections, kept small so the collector on the same VM always wins."""

from pathlib import Path

import duckdb

from .config import Settings


def connect(settings: Settings, path: Path | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path) if path else ":memory:")
    con.execute(f"SET memory_limit = '{settings.duckdb_memory}'")
    con.execute("SET threads = 1")
    con.execute("SET preserve_insertion_order = false")
    tmp = settings.derived_dir / "tmp" / "duckdb"
    tmp.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory = '{quote(tmp)}'")
    return con


def quote(path: Path | str) -> str:
    """A path for use inside a single-quoted SQL string."""
    return str(path).replace("\\", "/").replace("'", "''")
