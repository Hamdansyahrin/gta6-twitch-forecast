# gta6-twitch-forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Hamdansyahrin/gta6-twitch-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/Hamdansyahrin/gta6-twitch-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

A forecaster for a game launch day's Twitch viewership. One hour into
release day, with that hour's viewer count known, it forecasts each
remaining hour of the day (hours 2-24) as five quantiles
(0.05 / 0.25 / 0.5 / 0.75 / 0.95), scored by pinball loss in log space.

The method is empirical. Every case carries a library of past launches
with their full hourly curves, so the forecast is a weighted distribution
of how those days unfolded relative to their own hour-1 prints. Weights
reflect what kind of launch each comparable was (a late single global
unlock, a front-loaded simultaneous launch, or a rolling midnight-local
build), what kind of launch the case itself is -- read from its notes and
its pre-release history; an early-access launch leaves a busy category
days before release -- and how close the comparable's hour-1 audience is
to this case's in log space, because giant launches build far less over
the day than small ones. The empirical quantiles are blended with a
normal fit to steady the tails. Pure Python 3 standard library, offline
and deterministic: two runs on the same case are byte-identical.

## Run it

```sh
git clone https://github.com/Hamdansyahrin/gta6-twitch-forecast
cd gta6-twitch-forecast
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=output python3 main.py
```

Place case files (`*.in`, schema `makefun/twitch-ccu-case/v1`) in the
input directory; the program writes one `<id>.out` per case to the output
directory. Without the environment variables it reads `/input` and
writes `/output`.

---

<sub>Entered on [make.fun](https://make.fun/?ref=748tdgv2&utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
