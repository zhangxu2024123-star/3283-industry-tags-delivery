#!/usr/bin/env python3
"""Submit the 3283 delivery.

Guards:
  * default is a dry run — prints what would be sent, sends nothing
  * refuses if the task already has a delivery (latest_delivery_status is set),
    so a second run can never double-submit
  * refuses if the task is not in_progress
  * prints the API response verbatim

Usage:
  python submit.py            # dry run (default)
  python submit.py --yes      # actually submit
"""

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ENV = pathlib.Path(os.path.expanduser("~")) / ".clawhunt" / "agent.env"
PAYLOAD = HERE / "submit_payload.json"
PROBLEM_ID = 3283


def cfg():
    c = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            c[k.strip()] = v.strip()
    return c


def api(path, method="GET", body=None):
    c = cfg()
    base = c.get("CLAWHUNT_BASE_URL", "https://clawhunt.store").rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + c["CLAWHUNT_API_KEY"])
    req.add_header("User-Agent", "workbuddy-agent/1.0")
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


def main():
    yes = "--yes" in sys.argv
    payload = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    text = payload["solution_text"]

    print("problem      :", PROBLEM_ID)
    print("solution_text:", len(text), "chars (limit 10000)")
    print("attachments  :", len(payload.get("attachments") or []), "entries")
    print("assets       :", len(payload.get("assets") or []), "entries")
    print("first line   :", text.splitlines()[0][:70])

    if len(text) > 10000:
        print("\nABORT: solution_text exceeds 10000 chars")
        return 1

    try:
        cur = api(f"/api/v1/problems/{PROBLEM_ID}")["data"]
    except Exception as exc:
        print("\nABORT: cannot read task state:", repr(exc))
        return 1

    lds = cur.get("latest_delivery_status")
    if lds and lds != "rework_requested":
        print(f"\nABORT: task already has a delivery (status={lds}, "
              f"version={cur.get('latest_delivery_version')}).")
        print("Nothing sent. Only a rework_requested delivery may be resubmitted.")
        return 1
    if cur.get("status") != "in_progress":
        print(f"\nABORT: task status is {cur.get('status')!r}, expected 'in_progress'.")
        return 1

    print("task title   :", cur.get("title"))
    print("escrow       :", cur.get("escrow_status"), cur.get("escrow_amount"), "cents")
    print("channel      :", cur.get("delivery_channel"))
    print("assign dl    :", cur.get("assignment_deadline"))

    if not yes:
        print("\nDRY RUN — nothing sent. Re-run with --yes to submit.")
        return 0

    print("\nsubmitting ...")
    try:
        res = api(f"/api/v1/problems/{PROBLEM_ID}/solution", "POST", payload)
    except urllib.error.HTTPError as exc:
        print("HTTP", exc.code)
        print(exc.read().decode(errors="replace")[:2000])
        return 1

    print(json.dumps(res, ensure_ascii=False, indent=1)[:3000])
    data = res.get("data") or {}
    for k in ("delivery_status", "version", "status", "delivery_check", "problem_status"):
        if k in data:
            print(f"  {k} = {json.dumps(data[k], ensure_ascii=False)[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
