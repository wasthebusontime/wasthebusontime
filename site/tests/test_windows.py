import pytest

from wbot_site import windows
from wbot_site.windows import Window


def test_spans_in_words():
    assert Window(-60, 300).span == "1 min early to 5 min late"
    assert Window(0, 300).span == "0 to 5 min late"
    assert Window(-180, 420).span == "3 min early to 7 min late"
    assert Window(120, 300).span == "2 to 5 min late"


@pytest.mark.parametrize("minute, kind", [(-3, "early"), (-2, "early"), (-1, "on-time"), (0, "on-time"),
                                          (4, "on-time"), (5, "late"), (12, "late")])
def test_histogram_bucket_kinds(minute, kind):
    assert Window(-60, 300).kind(minute) == kind


def test_bucket_kinds_for_the_agency_window():
    assert [Window(0, 300).kind(m) for m in (-1, 0, 4, 5)] == ["early", "on-time", "on-time", "late"]


def test_from_meta_and_labels():
    w = windows.from_meta({"headline": [-60, 300], "alt": [0, 300]})
    assert w.headline_label == "On time (1 min early to 5 min late)"
    assert w.alt_label == "On time (Intercity Transit's own definition: 0 to 5 min late)"
    assert w.show_alt is windows.SHOW_ALT


@pytest.mark.parametrize("bad", [None, {}, {"headline": [-60, 300]}, {"headline": [-60, 330], "alt": [0, 300]},
                                 {"headline": [300, -60], "alt": [0, 300]}, {"headline": [-60.0, 300], "alt": [0, 300]}])
def test_from_meta_refuses_windows_it_cant_word(bad):
    with pytest.raises(ValueError):
        windows.from_meta(bad)


def test_map_legend_follows_the_bins(monkeypatch):
    assert windows.map_legend() == ["90% or more on time", "80 to 89%", "70 to 79%", "Under 70%"]
    monkeypatch.setattr(windows, "MAP_BINS", (95, 85))
    assert windows.map_legend() == ["95% or more on time", "85 to 94%", "Under 85%"]
