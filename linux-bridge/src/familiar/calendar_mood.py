"""Time-of-day / day-of-week mood for the idle Tidbyt, ported from the M5's
clock-mode calendar (src/main.cpp). Pure function of the given datetime."""


def calendar_mood(dt):
    """Return (baseline, flourish | None) for local datetime `dt`.

    baseline is the calm mood held most of the time; flourish (if any) is the
    brief periodic accent. Both are Tidbyt state/asset names. Precedence
    matches the firmware: 1-7am beats weekend beats the rest.
    """
    h, wd = dt.hour, dt.weekday()          # weekday(): Mon=0 .. Sun=6
    weekend, friday = wd >= 5, wd == 4
    if 1 <= h < 7:          return ("sleep", None)
    if weekend:             return ("sleep", "heart")
    if h < 9:               return ("sleep", "idle")     # early morning, incl. midnight
    if h == 12:             return ("idle",  "heart")
    if friday and h >= 15:  return ("idle",  "celebrate")  # TGIF through 11:59pm
    if h >= 22:             return ("sleep", "dizzy")     # 10-11:59pm
    return ("idle", "sleep")
