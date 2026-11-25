#!/usr/bin/env python3
"""Sync the QA plan Google Sheet with this repo.

  sheet_sync.py check
      Compare the sheet with the tests. Every TestID marked "Automated" needs a test
      tagged @pytest.mark.case("<TestID>"), and every tagged test needs a TestID in
      the sheet. Exit 2 on a mismatch (Jenkins marks the build UNSTABLE).

  sheet_sync.py publish --results results/results.json --release v0.792 \
      [--build-url URL] [--tested "v0.792 qa @ b21b6195"]
      Write a "Results <release>" tab: one row per TestID with PASS / FAIL / SKIP for
      automated tests. Rows that aren't automated keep whatever a tester already typed
      into Result and Notes, so re-running a release doesn't wipe manual results.

Auth: GOOGLE_APPLICATION_CREDENTIALS points at either
  - an authorized-user file for the omnibot Google user (made once with
    tools/google_login.py; holds a refresh token), or
  - a service-account JSON key.
Whichever account it is, the sheet must be shared with it as an editor.
"""
import argparse
import glob
import json
import os
import re
import sys
import time

DEFAULT_SHEET_ID = "1oIHJG7mPBczIw56sr2qffSUKscZsVlx3xCgQEtrGGYE"
PLAN_TAB = "Sheet1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER = ["TestID", "Test", "Automation", "Result", "Details", "Duration (s)", "Run at", "Build", "Notes"]


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def credentials(path):
    """Load a service-account key or an authorized-user (refresh token) file."""
    with open(path) as f:
        data = json.load(f)
    kind = data.get("type")
    if kind == "service_account":
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    if kind == "authorized_user":
        from google.oauth2.credentials import Credentials
        return Credentials.from_authorized_user_file(path, scopes=SCOPES)
    if "installed" in data or "web" in data:
        sys.exit("The Google credential is an OAuth *client* file (client_secret_*.json), not the omnibot "
                 "sign-in file. Run tools/google_login.py with it and store that output instead.")
    sys.exit("The Google credential has unknown type {!r} (top-level keys: {}); expected a "
             "service_account key or the authorized_user file from tools/google_login.py".format(
                 kind, ", ".join(sorted(data))))


def service():
    from googleapiclient.discovery import build
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        sys.exit("GOOGLE_APPLICATION_CREDENTIALS is not set (path to the omnibot credential file)")
    return build("sheets", "v4", credentials=credentials(path), cache_discovery=False).spreadsheets()


def plan_cases(sheets, sheet_id):
    """[{id, test, automation}] from the plan tab (header row 1: TestID, Test, Priority, Step, Automation)."""
    rows = sheets.values().get(spreadsheetId=sheet_id, range="{}!A2:E".format(PLAN_TAB)).execute().get("values", [])
    cases = []
    for r in rows:
        r = r + [""] * (5 - len(r))
        if r[0].strip():
            cases.append({"id": r[0].strip(), "test": r[1].strip(), "automation": r[4].strip()})
    return cases


def repo_cases():
    """{TestID: [test files]} from @pytest.mark.case("...") tags in tests/."""
    found = {}
    for path in glob.glob(os.path.join(ROOT, "tests", "test_*.py")):
        for cid in re.findall(r"mark\.case\(\s*[\"']([^\"']+)[\"']", open(path).read()):
            found.setdefault(cid, []).append(os.path.relpath(path, ROOT))
    return found


def cmd_check(a):
    cases = plan_cases(service(), a.sheet)
    tagged = repo_cases()
    ids = {c["id"] for c in cases}
    missing = [c["id"] for c in cases if c["automation"] == "Automated" and c["id"] not in tagged]
    unknown = sorted(set(tagged) - ids)
    not_marked = [c["id"] for c in cases if c["id"] in tagged and c["automation"] != "Automated"]
    for cid in missing:
        print("MISSING TEST  {} is marked Automated in the sheet but no test is tagged with it".format(cid))
    for cid in unknown:
        print("UNKNOWN ID    {} is tagged in {} but isn't in the sheet".format(cid, ", ".join(tagged[cid])))
    for cid in not_marked:
        print("NOTE          {} has tests but the sheet says it isn't Automated".format(cid))
    auto = sum(1 for c in cases if c["automation"] == "Automated")
    print("{} test cases in the sheet, {} marked Automated, {} tagged in the repo".format(len(cases), auto, len(tagged)))
    sys.exit(2 if missing or unknown else 0)


def aggregate(results):
    """Per TestID: FAIL if any test failed/errored, else PASS if any passed, else SKIP."""
    out = {}
    for r in results:
        e = out.setdefault(r["case"], {"outcomes": [], "details": [], "duration": 0.0, "finished": ""})
        e["outcomes"].append(r["outcome"])
        e["duration"] += r.get("duration") or 0
        e["finished"] = max(e["finished"], r.get("finished") or "")
        if r["outcome"] in ("failed", "error", "skipped") and r.get("details"):
            e["details"].append("{}: {}".format(r["nodeid"].split("::")[-1], r["details"]))
    for e in out.values():
        o = e["outcomes"]
        e["result"] = "FAIL" if any(x in ("failed", "error") for x in o) else "PASS" if "passed" in o else "SKIP"
    return out


def cmd_publish(a):
    sheets = service()
    cases = plan_cases(sheets, a.sheet)
    results = aggregate(json.load(open(a.results)))
    tab = "Results {}".format(a.release)

    meta = sheets.get(spreadsheetId=a.sheet, fields="sheets.properties").execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    previous = {}
    if tab in titles:
        rows = sheets.values().get(spreadsheetId=a.sheet, range="'{}'!A1:I".format(tab)).execute().get("values", [])
        at = next((i for i, r in enumerate(rows) if r[:1] == ["TestID"]), None)
        hdr = rows[at] if at is not None else HEADER
        for r in (rows[at + 1:] if at is not None else []):
            r = r + [""] * (len(hdr) - len(r))
            if r and r[0]:
                previous[r[0]] = dict(zip(hdr, r))
    else:
        sheets.batchUpdate(spreadsheetId=a.sheet, body={"requests": [
            {"addSheet": {"properties": {"title": tab, "gridProperties": {"frozenRowCount": 2}}}}]}).execute()

    stamp = time.strftime("%Y-%m-%d %H:%M")
    values = [["Tested: {}  |  Updated: {}  |  {}".format(a.tested or a.release, stamp, a.build_url or "")], HEADER]
    for c in cases:
        prev = previous.get(c["id"], {})
        r = results.get(c["id"])
        if r:
            values.append([c["id"], c["test"], c["automation"], r["result"], "\n".join(r["details"])[:5000],
                           round(r["duration"], 1), r["finished"], a.build_url or "", prev.get("Notes", "")])
        else:
            # not run this time: keep the earlier or manually entered result if there is one
            values.append([c["id"], c["test"], c["automation"],
                           prev.get("Result") or ("NOT RUN" if c["automation"] == "Automated" else "MANUAL"),
                           prev.get("Details", ""), prev.get("Duration (s)", ""), prev.get("Run at", ""),
                           prev.get("Build", ""), prev.get("Notes", "")])
    sheets.values().clear(spreadsheetId=a.sheet, range="'{}'!A1:I".format(tab)).execute()
    sheets.values().update(spreadsheetId=a.sheet, range="'{}'!A1".format(tab),
                           valueInputOption="RAW", body={"values": values}).execute()
    counts = {}
    for c in cases:
        res = results.get(c["id"], {}).get("result")
        if res:
            counts[res] = counts.get(res, 0) + 1
    print("Wrote {} rows to '{}': {}".format(len(cases), tab, counts))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet", default=os.environ.get("QA_SHEET_ID", DEFAULT_SHEET_ID))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    p = sub.add_parser("publish")
    p.add_argument("--results", required=True)
    p.add_argument("--release", required=True)
    p.add_argument("--build-url")
    p.add_argument("--tested", help="what was tested, e.g. 'v0.792 qa @ b21b6195'")
    a = ap.parse_args()
    {"check": cmd_check, "publish": cmd_publish}[a.cmd](a)


if __name__ == "__main__":
    main()
