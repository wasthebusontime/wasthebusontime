"""Shared fixtures. All feed data here is synthetic (see synth.py); no real feed data is committed."""

from datetime import date

import pytest
from synth import Feed, default_trips, write_gtfs

from wbot_pipeline import timeutil
from wbot_pipeline.config import load_settings

DAY = date(2026, 10, 6)  # a Tuesday


@pytest.fixture
def settings(tmp_path):
    return load_settings({}, data_dir=tmp_path / "data", derived_dir=tmp_path / "derived",
                         first_service_date=DAY)


@pytest.fixture
def trips():
    return default_trips()


@pytest.fixture
def network(settings, trips):
    write_gtfs(settings.static_dir, "test1_aaaaaaaa", trips)
    return trips


@pytest.fixture
def feed(settings, network):
    return Feed(settings.data_dir, DAY, network)


def after(d: date) -> int:
    """A time when the service date is long finished and packed."""
    return timeutil.origin(d) + 36 * 3600
