from datetime import datetime
from familiar.calendar_mood import calendar_mood


def test_deep_night_sleeps_no_flourish():
    assert calendar_mood(datetime(2026, 7, 8, 3, 0)) == ("sleep", None)     # Wed 3am


def test_deep_night_beats_weekend():
    assert calendar_mood(datetime(2026, 7, 12, 3, 0)) == ("sleep", None)    # Sun 3am


def test_weekend_naps_with_heart():
    assert calendar_mood(datetime(2026, 7, 11, 14, 0)) == ("sleep", "heart")  # Sat 2pm


def test_weekend_beats_noon():
    assert calendar_mood(datetime(2026, 7, 12, 12, 30)) == ("sleep", "heart") # Sun 12:30


def test_weekend_late_night_still_heart():
    # Weekend precedes the late-night dizzy branch: Sat 11pm stays weekend-heart.
    assert calendar_mood(datetime(2026, 7, 11, 23, 0)) == ("sleep", "heart")  # Sat 11pm


def test_early_weekday_sleeps_with_idle():
    assert calendar_mood(datetime(2026, 7, 6, 8, 0)) == ("sleep", "idle")   # Mon 8am


def test_noon_weekday_hearts():
    assert calendar_mood(datetime(2026, 7, 6, 12, 15)) == ("idle", "heart") # Mon 12:15


def test_friday_afternoon_tgif():
    assert calendar_mood(datetime(2026, 7, 10, 16, 0)) == ("idle", "celebrate")  # Fri 4pm


def test_friday_before_3pm_is_normal_day():
    assert calendar_mood(datetime(2026, 7, 10, 14, 0)) == ("idle", "sleep")  # Fri 2pm


def test_late_night_dizzy():
    assert calendar_mood(datetime(2026, 7, 8, 23, 0)) == ("sleep", "dizzy") # Wed 11pm


def test_midnight_hour_is_early_morning_idle():
    # Midnight (hour 0) falls into the early-morning group on the M5, not dizzy.
    assert calendar_mood(datetime(2026, 7, 8, 0, 30)) == ("sleep", "idle")  # Wed 00:30


def test_friday_night_still_tgif():
    # TGIF runs through 11:59pm; celebrate beats the late-night dizzy on the M5.
    assert calendar_mood(datetime(2026, 7, 10, 22, 0)) == ("idle", "celebrate")  # Fri 10pm


def test_normal_daytime_idle():
    assert calendar_mood(datetime(2026, 7, 7, 14, 0)) == ("idle", "sleep")  # Tue 2pm
