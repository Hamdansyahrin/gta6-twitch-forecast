#!/usr/bin/env python3
"""Forecast a game launch day's Twitch viewers, hour by hour.

Reads Twitch category cases (schema makefun/twitch-ccu-case/v1): the viewer
count one hour into release day is known, and the job is to forecast each
remaining hour of the day (hours 2-24) as five quantiles
(0.05 / 0.25 / 0.5 / 0.75 / 0.95), scored by pinball loss in log space.

Method, in one paragraph: every case carries a library of past launches with
their full hourly curves. For each past launch we know how its day unfolded
relative to its own hour-1 print, so the forecast for a new launch is a
weighted empirical distribution of those log-ratio curves, hour by hour.
Weights reflect what kind of launch each comparable was (a late single global
unlock, a front-loaded simultaneous launch, or a rolling midnight-local
build), what kind of launch the case itself is (read from its notes and its
pre-release history: an early-access launch leaves a busy category days
before release day), and how close the comparable's hour-1 audience is to
this case's in log space -- giant launches build far less over the day than
small ones, and tiny hour-1 prints belong to games that had not unlocked yet.
The empirical quantiles are blended 50/50 with a normal fit (median and a
robust spread) to steady the tails. Deterministic, standard library only.
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta

Z = (-1.6448536269514722, -0.6744897501960817, 0.0,
     0.6744897501960817, 1.6448536269514722)
TAUS = (0.05, 0.25, 0.5, 0.75, 0.95)
KERNEL_BW = 1.0     # bandwidth of the ln(hour-1 viewers) similarity kernel
TAIL_BLEND = 0.5    # weight of the normal fit in the final quantiles


def parse_ts(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def comp_curve(comp):
    """A comparable's release-day curve: {hour 1..24: viewers}."""
    try:
        start = parse_ts(comp["day_start"])
    except Exception:
        return {}
    pts = {}
    for point in comp.get("hourly") or []:
        try:
            off = (parse_ts(point["from"]) - start).total_seconds() / 3600.0
            viewers = float(point["viewers"])
        except Exception:
            continue
        if 0 <= off < 24 and off == int(off):
            pts[int(off) + 1] = viewers
    return pts


def classify(curve, unlock_text):
    """Sort a comparable's day curve into LATE / DECAY / BUILD.

    LATE: the game unlocked partway through the UTC day, so hour 1 is
    pre-unlock noise and the curve jumps violently at the unlock hour.
    DECAY: the peak came in the first hours and midday sagged well below
    hour 1 (a simultaneous or PC-led launch). BUILD: everything else --
    the rolling midnight-local shape that builds toward the evening.
    Also flags early access, whose standard-edition day is damped because
    part of the audience played days earlier.
    """
    if len(curve) < 20 or curve.get(1, 0) <= 0:
        return None, False
    p1 = curve[1]
    argmax = max(curve, key=curve.get)
    max_jump = 1.0
    for h in range(3, 25):
        if h in curve and (h - 1) in curve and curve[h - 1] > 0:
            max_jump = max(max_jump, curve[h] / curve[h - 1])
    ea = "early access" in (unlock_text or "").lower()
    if max_jump >= 4.0:
        return "LATE", ea
    if argmax <= 3 and curve.get(12, p1) < 0.75 * p1:
        return "DECAY", ea
    return "BUILD", ea


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def case_regime(case, p1):
    """CLEAN / EA / LATE for the case itself, from its notes and history.

    Early access is visible in the data: the category was already busy
    48-72 hours before release day. A rolling midnight-local launch with
    no early access is CLEAN -- the regime this method is built around.
    """
    notes = (case.get("notes") or "").lower()
    rolling = ("midnight local" in notes or "rolling" in notes
               or "midnight in each" in notes)
    if rolling and "no early access" in notes:
        return "CLEAN"
    try:
        start = parse_ts(case["day_start"])
    except Exception:
        return "CLEAN"
    pre24, pre72 = [], []
    for point in case.get("history_hourly") or []:
        try:
            t = parse_ts(point["from"])
            v = float(point["viewers"])
        except Exception:
            continue
        if start - timedelta(hours=24) <= t < start:
            pre24.append(v)
        elif start - timedelta(hours=72) <= t < start - timedelta(hours=24):
            pre72.append(v)
    med24 = _median(pre24)
    med72 = _median(pre72)
    if med72 is not None and med72 > 0 and med72 / p1 >= 0.15:
        return "EA"
    if rolling:
        return "CLEAN"
    if (med24 is not None and med24 > 0 and p1 <= 3.0 * med24
            and (not pre24 or max(pre24) <= 1.2 * p1)):
        return "LATE"
    return "CLEAN"


# Class weights per case regime; early-access comparables are reweighted by
# EA_FACTOR (damped curves, trusted only when the case itself had early
# access).
BASE_W = {
    "CLEAN": {"BUILD": 1.0, "DECAY": 0.30, "LATE": 0.0},
    "EA": {"BUILD": 0.6, "DECAY": 0.4, "LATE": 0.1},
    "LATE": {"BUILD": 0.2, "DECAY": 0.1, "LATE": 1.0},
}
EA_FACTOR = {"CLEAN": 0.45, "EA": 1.6, "LATE": 1.0}


def wquantile(sorted_pairs, tau):
    """Weighted quantile of [(value, weight)] sorted by value."""
    tot = sum(w for _, w in sorted_pairs)
    if tot <= 0:
        return sorted_pairs[len(sorted_pairs) // 2][0]
    target = tau * tot
    acc = 0.0
    prev_v, prev_c = sorted_pairs[0][0], 0.0
    for v, w in sorted_pairs:
        acc += w
        if acc >= target:
            if acc == w:
                return v
            frac = (target - prev_c) / w if w > 0 else 0.0
            return prev_v + frac * (v - prev_v)
        prev_v, prev_c = v, acc
    return sorted_pairs[-1][0]


def forecast(case, p1):
    """{hour 2..24: [q05, q25, q50, q75, q95]} for one case."""
    regime = case_regime(case, p1)
    weights = BASE_W[regime]
    ea_factor = EA_FACTOR[regime]
    log_p1 = math.log(p1)
    samples = {h: [] for h in range(2, 25)}
    for comp in case.get("comparables") or []:
        if "Grand Theft Auto VI" in (comp.get("name") or ""):
            continue  # the pre-release category itself has no launch day
        curve = comp_curve(comp)
        cls, ea = classify(curve, comp.get("unlock", ""))
        if cls is None:
            continue
        w = weights.get(cls, 0.3)
        if ea:
            w *= ea_factor
        dist = (log_p1 - math.log(curve[1])) / KERNEL_BW
        w *= math.exp(-0.5 * dist * dist)
        if w <= 1e-9:
            continue
        cp1 = curve[1]
        for h in range(2, 25):
            if h in curve and curve[h] > 0:
                samples[h].append((math.log(curve[h] / cp1), w))
    out = {}
    for h in range(2, 25):
        sp = sorted(samples[h])
        if not sp:
            out[h] = [p1] * 5
            continue
        qs = [wquantile(sp, t) for t in TAUS]
        med = qs[2]
        spread = max(0.08, (qs[3] - qs[1]) / (2 * 0.6744897501960817) * 0.6
                     + (qs[4] - qs[0]) / (2 * 1.6448536269514722) * 0.4)
        blended = [(1.0 - TAIL_BLEND) * qs[i]
                   + TAIL_BLEND * (med + Z[i] * spread) for i in range(5)]
        row = []
        for x in sorted(blended):
            v = p1 * math.exp(x)
            row.append(min(1e9, max(1.0, math.floor(v * 100 + 0.5) / 100.0)))
        out[h] = row
    return out


def round_js(x):
    """Round half up to two decimals, as the house starter does."""
    return math.floor(x * 100 + 0.5) / 100.0


def hour_one_print(case):
    """The known hour-1 viewer count, with fallbacks so we never crash."""
    for row in case.get("known") or []:
        try:
            if int(row.get("hour", 1)) == 1:
                v = float(row["viewers"])
                if v > 0:
                    return v
        except Exception:
            continue
    for row in case.get("known") or []:
        try:
            v = float(row["viewers"])
            if v > 0:
                return v
        except Exception:
            continue
    for key in ("history_hourly", "history"):
        rows = case.get(key) or []
        if rows:
            try:
                v = float(rows[-1]["viewers"])
                if v > 0:
                    return v
            except Exception:
                pass
    return 1.0


def baseline(p1, hours):
    """The house baseline, kept as the fallback: flat median at the hour-1
    print, lognormal spread 0.15 * sqrt(h - 1)."""
    out = {}
    for h in hours:
        s = 0.15 * math.sqrt(max(h - 1, 0))
        out[h] = [min(1e9, max(1.0, round_js(p1 * math.exp(z * s)))) for z in Z]
    return out


def num(x):
    r = round_js(float(x))
    if r == int(r):
        return str(int(r))
    return repr(r)


def json_out(hours_map, hours):
    parts = []
    for h in hours:
        row = hours_map[h]
        parts.append('{"hour":%d,"q":[%s]}' % (h, ",".join(num(v) for v in row)))
    return '{"hours":[' + ",".join(parts) + "]}"


def run_case(case):
    """Forecast one case; degrade to the baseline, then to flat, on error."""
    p1 = hour_one_print(case)
    hours = []
    for row in case.get("forecast") or []:
        try:
            hours.append(int(row["hour"]))
        except Exception:
            continue
    hours = sorted(set(hours)) or list(range(2, 25))
    try:
        fc = forecast(case, p1)
        picked = {}
        for h in hours:
            src = min(24, max(2, h))
            picked[h] = fc[src]
        return json_out(picked, hours)
    except Exception:
        pass
    try:
        return json_out(baseline(p1, hours), hours)
    except Exception:
        flat = {h: [p1] * 5 for h in hours}
        return json_out(flat, hours)


def main():
    in_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)
    try:
        names = sorted(n for n in os.listdir(in_dir) if n.endswith(".in"))
    except Exception:
        names = []
    for name in names:
        path = os.path.join(in_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                case = json.load(fh)
        except Exception:
            continue
        cid = case.get("id") or name[:-3]
        try:
            text = run_case(case)
        except Exception:
            text = '{"hours":[]}'
        with open(os.path.join(out_dir, cid + ".out"), "w",
                  encoding="utf-8") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
