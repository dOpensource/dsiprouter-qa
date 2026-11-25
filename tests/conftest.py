"""Shared fixtures for the dSIPRouter QA suite.

Each test is tagged with the TestID from the QA plan sheet:

    @pytest.mark.case("ROUTE-001")

That ID is how results get written back to the sheet (tools/sheet_sync.py).
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dsipqa.config import PREFIX, Config  # noqa: E402
from dsipqa.gui import DsipGui  # noqa: E402
from dsipqa.qahost import QaHost  # noqa: E402
from dsipqa.undo import Undo  # noqa: E402

_RESULTS = []


@pytest.fixture(scope="session")
def cfg():
    c = Config()
    os.makedirs(c.outdir, exist_ok=True)
    return c


@pytest.fixture(scope="session")
def qa(cfg):
    return QaHost(cfg.host, cfg.ssh_user, cfg.ssh_key, known_hosts=os.path.join(cfg.outdir, "known_hosts"))


@pytest.fixture(scope="session", autouse=True)
def tested_build(cfg, qa):
    """Record which dSIPRouter version/commit is on the QA box (results/tested.json)."""
    try:
        ver, rev = qa.dsip_version()
    except Exception as e:  # noqa: BLE001 - don't block the run on this
        ver, rev = "", "unknown ({})".format(e)
    info = {"host": cfg.host, "version": ver, "revision": rev}
    with open(os.path.join(cfg.outdir, "tested.json"), "w") as f:
        json.dump(info, f)
    print("\nTesting dSIPRouter {} ({}) on {}".format(ver or "?", rev, cfg.host))
    return info


@pytest.fixture(scope="session")
def agent_ip(cfg, qa):
    """Public IP our SIPp traffic comes from (the 'carrier' address in inbound tests)."""
    return cfg.agent_ip or qa.client_ip()


@pytest.fixture(scope="session")
def gui_session(cfg, qa):
    g = DsipGui(cfg.host, cfg.gui_user, cfg.gui_password, port=cfg.gui_port, insecure=cfg.tls_insecure)
    g.login()
    sweep_leftovers(g, qa)
    return g


def pytest_sessionstart(session):
    # Jenkins "Abort" sends SIGTERM. Turn it into KeyboardInterrupt so pytest unwinds and
    # each test's undo stack still runs, instead of dying with QA half-changed.
    import signal

    def _term(signum, frame):
        raise KeyboardInterrupt("SIGTERM (build aborted)")
    signal.signal(signal.SIGTERM, _term)


@pytest.fixture
def gui(gui_session):
    # sessions can be dropped by a dsiprouter restart (CLI tests); log in fresh each test
    return gui_session.login()


@pytest.fixture
def undo(gui, request):
    """Cleanup stack; a reload is always queued first so it runs last."""
    u = Undo()
    u.push("reload kamailio", gui.reload_kamailio)
    yield u
    failures = u.run()
    if failures:
        request.node.user_properties.append(("cleanup_failures", failures))
        pytest.fail("CLEANUP FAILED, QA box left changed: " + "; ".join(failures), pytrace=False)


@pytest.fixture
def outdir(cfg, request):
    d = os.path.join(cfg.outdir, request.node.name)
    os.makedirs(d, exist_ok=True)
    return d


DEFAULT_ROUTE_STATE = "/var/lib/dsipqa/default_outbound.json"


def sweep_leftovers(gui, qa):
    """Undo what a killed run left behind, before any test starts:

    - the Default Outbound Route: QA's real setting is saved on the QA box the first time
      it's seen in a clean state; if a killed test left the route pointing at a QA-AUTO
      group (or deleted it), it's put back from that file
    - objects named QA-AUTO*, and stray packet captures
    Never touches anything else.
    """
    removed = []
    ours = {str(g["gwgroupid"]) for g in gui.carrier_groups() if str(g.get("name") or "").startswith(PREFIX)}
    route = gui.default_outbound_route()
    current_gw = str(route.get("gwgroupid")).lstrip("#") if route else None
    rc, saved, _ = qa.run("cat {} 2>/dev/null".format(DEFAULT_ROUTE_STATE), check=False)
    saved = json.loads(saved) if rc == 0 and saved.strip() else None
    real_groups = {str(g["gwgroupid"]) for g in gui.carrier_groups()} - ours
    if route is None and saved and saved.get("gwgroupid") in real_groups:
        # a killed ROUTE-003/009 deleted the default route; put it back
        gui.set_outbound_route("", saved["gwgroupid"], saved["name"])
        removed.append("default outbound route re-created -> {}".format(saved["gwgroupid"]))
    elif current_gw not in ours:
        # clean state (a route to a real carrier, or no default route at all on a fresh install):
        # remember it; this also picks up a deliberate change someone made to QA's default
        state = ({"gwgroupid": current_gw, "name": route.get("name") or "Default Outbound Route"}
                 if route else {"gwgroupid": None, "name": None})
        if state != saved:
            qa.run("mkdir -p {} && cat > {}".format(os.path.dirname(DEFAULT_ROUTE_STATE), DEFAULT_ROUTE_STATE),
                   stdin=json.dumps(state))
    elif saved:
        # a killed test left the default route pointing at one of our groups
        if saved["gwgroupid"] is None:
            gui.delete_outbound_route(route["ruleid"])
            removed.append("temporary default outbound route deleted")
        else:
            gui.set_outbound_route(route["ruleid"], saved["gwgroupid"], saved["name"])
            removed.append("default outbound route restored -> {}".format(saved["gwgroupid"]))
    else:
        raise RuntimeError("Default Outbound Route points at a test group and there's no saved original in "
                           "{} on QA; fix it in the GUI before running the suite".format(DEFAULT_ROUTE_STATE))
    qa.run("pkill -f 'tcpdump .*dst host 192\\.0\\.2\\.' ; rm -f /tmp/dsipqa-cap-*.txt", check=False)
    for r in gui.inbound_routes():
        if r.get("rulename", "").startswith(PREFIX):
            gui.delete_inbound_route(r)
            removed.append("inbound route {}".format(r["ruleid"]))
    for r in gui.outbound_routes():
        if str(r.get("name") or "").startswith(PREFIX):
            gui.delete_outbound_route(r["ruleid"])
            removed.append("outbound route {}".format(r["ruleid"]))
    for g in gui.endpoint_groups():
        if str(g.get("name") or "").startswith(PREFIX):
            gui.delete_endpoint_group(g["gwgroupid"])
            removed.append("endpoint group {}".format(g["gwgroupid"]))
    for g in gui.carrier_groups():
        if str(g.get("name") or "").startswith(PREFIX):
            gui.delete_carrier_group(g["gwgroupid"])
            removed.append("carrier group {}".format(g["gwgroupid"]))
    if removed:
        gui.reload_kamailio()
        print("Removed leftovers from an earlier run: " + ", ".join(removed))


# ---- result recording ------------------------------------------------------

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    case = item.get_closest_marker("case")
    if not case:
        return
    case_id = case.args[0]
    entry = next((r for r in _RESULTS if r["nodeid"] == item.nodeid), None)
    if entry is None:
        entry = {"case": case_id, "nodeid": item.nodeid, "outcome": "passed", "details": "",
                 "duration": 0.0, "finished": None}
        _RESULTS.append(entry)
    entry["duration"] += rep.duration
    entry["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if rep.skipped and entry["outcome"] == "passed":
        entry["outcome"] = "skipped"
        entry["details"] = str(rep.longrepr[-1]) if isinstance(rep.longrepr, tuple) else str(rep.longrepr)
    elif rep.failed:
        entry["outcome"] = "failed" if rep.when == "call" else "error"
        msg = getattr(rep, "longreprtext", "") or str(rep.longrepr)
        lines = [ln for ln in msg.splitlines() if ln.startswith("E ") or "CLEANUP" in ln]
        entry["details"] = ("\n".join(lines) or msg)[-1500:]


def pytest_sessionfinish(session, exitstatus):
    outdir = Config().outdir
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "results.json"), "w") as f:
        json.dump(_RESULTS, f, indent=2)

