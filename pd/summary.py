#!/usr/bin/env python3
# Prints the main results of a LibreLane run.
# usage: pd/summary.py [workspace or run dir, default .]
# Given a workspace it reads the newest run in librelane/runs.
import json
import os
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "."
if not os.path.exists(os.path.join(path, "resolved.json")):
    runs = os.path.join(path, "librelane", "runs")
    path = os.path.join(runs, sorted(os.listdir(runs))[-1])

m = json.load(open(os.path.join(path, "final", "metrics.json")))
period = json.load(open(os.path.join(path, "resolved.json")))["CLOCK_PERIOD"]


def get(key):
    return m.get(key, "n/a")


def ns(key):
    v = m.get(key)
    return "n/a" if v is None else f"{v:+.2f} ns"


ss = m.get("timing__setup__ws__corner:max_ss_125C_3v00")
tt = m.get("timing__setup__ws__corner:max_tt_025C_3v30")
fmax = lambda ws: "n/a" if ws is None else f"{1000 / (period - ws):.1f} MHz"

print(f"run          {os.path.basename(os.path.abspath(path))}")
print(f"clock        {period} ns ({1000 / period:.1f} MHz)")
print(f"setup ss     {ns('timing__setup__ws__corner:max_ss_125C_3v00')}  "
      f"({get('timing__setup_vio__count')} violations, fmax {fmax(ss)})")
print(f"setup tt     {ns('timing__setup__ws__corner:max_tt_025C_3v30')}  (fmax {fmax(tt)})")
print(f"hold worst   {ns('timing__hold__ws')}  ({get('timing__hold_vio__count')} violations)")
print(f"stdcell area {get('design__instance__area__stdcell') / 1e6:.3f} mm2, "
      f"utilization {get('design__instance__utilization__stdcell') * 100:.1f}%")
print(f"DRC          route {get('route__drc_errors')}, "
      f"klayout {get('klayout__drc_error__count')}, magic {get('magic__drc_error__count')}")
print(f"LVS          {get('design__lvs_error__count')}")
print(f"antenna      {get('antenna__violating__nets')} nets")
