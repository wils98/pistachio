import pandas as pd


def interpolate_lookup(value, table: dict):
    """
    Linearly interpolates between the two nearest keys in `table` for `value`.
    Keys may be int or str numerals; values may be plain numbers or dicts of
    numbers (interpolated component-wise). Past either edge of the table, this
    extrapolates using the slope of the two outermost keys rather than
    clamping to the edge value — clamping collapsed every player above/below
    the table's outermost bucket onto one identical adjustment (see NOTES.md's
    "pitchers maxed at 3.9" bug: ~4% of players share the same rounded-to-80
    scaled rating, and 80 was the table's own ceiling with no headroom above
    it). A missing (NaN) rating maps exactly onto the table's lowest key, same
    as before — no further extrapolation below it.
    """
    keys = sorted(table.keys(), key=float)

    if pd.isna(value):
        value = float(keys[0])
    else:
        value = float(value)

    if value <= float(keys[0]):
        lo, hi = keys[0], keys[1]
    elif value >= float(keys[-1]):
        lo, hi = keys[-2], keys[-1]
    else:
        lo, hi = next(
            (keys[i], keys[i + 1])
            for i in range(len(keys) - 1)
            if float(keys[i]) <= value <= float(keys[i + 1])
        )

    x0, x1 = float(lo), float(hi)
    frac = (value - x0) / (x1 - x0)
    y0, y1 = table[lo], table[hi]

    if isinstance(y0, dict):
        return {k: y0[k] + (y1[k] - y0[k]) * frac for k in y0}
    return y0 + (y1 - y0) * frac
