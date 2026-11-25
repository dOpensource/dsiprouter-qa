"""Outbound routing: softphone -> dSIPRouter -> carrier.

ROUTE-001 and ROUTE-002 place real PSTN calls (billed, they ring a real number).
ROUTE-003 and ROUTE-009 send calls to an unrouted test address and check the
routing decision on the QA box, so they cost nothing.
"""
import os

import pytest

from dsipqa.config import BLACKHOLE
from dsipqa.sip import softphone_call
from dsipqa.steps import (make_carrier_group, make_softphone, name, outbound_routes_matching,
                          remove_default_outbound_route, swap_default_outbound_route)

NO_RULES = "500 No rules defined for number"


@pytest.mark.case("ROUTE-001")
@pytest.mark.pstn
def test_route_001_flowroute_outbound(cfg, gui, undo, outdir):
    """Softphone with user/pass auth calls out through an IP-auth Flowroute carrier group
    (tech prefix 16723617*) set as the Default Outbound Route.
    Pass: REGISTER 200, INVITE answered, held 10 s, BYE 200.
    Prerequisite (plan step 1): QA's public IP is allowed on the Flowroute test account."""
    carrier = make_carrier_group(gui, undo, "Flowroute", [cfg.flowroute_host], prefix=cfg.flowroute_prefix)
    phone = make_softphone(gui, undo, "3134470158")
    swap_default_outbound_route(gui, undo, carrier)
    gui.reload_kamailio()

    r = softphone_call(cfg.host, phone["user"], phone["password"], phone["domain"], cfg.dial_number,
                       os.path.join(outdir, "sipp"), hold_s=10, local_port=cfg.sipp_port)
    assert r["passed"], "{} (Flowroute 403 = QA IP or tech prefix not on the Flowroute ACL)".format(r["reason"])


@pytest.mark.case("ROUTE-002")
@pytest.mark.pstn
def test_route_002_signalwire_outbound(cfg, gui, undo, outdir):
    """Softphone calls out through a SignalWire carrier group that uses user/pass auth."""
    if not (cfg.signalwire_user and cfg.signalwire_pass):
        pytest.skip("SIGNALWIRE_USER / SIGNALWIRE_PASS not set (add them as a Jenkins credential)")
    gid = gui.create_carrier_group(name("SignalWire"), auth_type="userpwd",
                                   r_username=cfg.signalwire_user, auth_username=cfg.signalwire_user,
                                   auth_password=cfg.signalwire_pass, auth_domain=cfg.signalwire_host,
                                   auth_proxy=cfg.signalwire_host)
    undo.push("delete carrier group {}".format(gid), lambda: gui.delete_carrier_group(gid))
    gui.add_carrier(gid, name("SignalWire c1"), cfg.signalwire_host)
    swap_default_outbound_route(gui, undo, gid)
    phone = make_softphone(gui, undo, "3134860408")
    gui.reload_kamailio()

    r = softphone_call(cfg.host, phone["user"], phone["password"], phone["domain"], cfg.dial_number,
                       os.path.join(outdir, "sipp"), hold_s=10, local_port=cfg.sipp_port)
    assert r["passed"], r["reason"]


@pytest.mark.case("ROUTE-003")
def test_route_003_global_outbound_routes(cfg, gui, qa, undo, outdir):
    """An outbound route with from prefix 313 and to prefix 1888 sends matching calls to its
    carrier group; a call that doesn't match any route gets "500 No rules defined for number".

    Plan says "remove all outbound routes"; we remove only the Default Outbound Route
    (the catch-all) and check that no other existing route matches our numbers, so QA's
    other routes stay untouched."""
    matching = "18889072085"
    non_matching = "15555550142"
    clash = outbound_routes_matching(gui, matching) + outbound_routes_matching(gui, non_matching)
    assert not clash, "existing outbound routes would match the test numbers: {}".format(clash)

    remove_default_outbound_route(gui, undo)
    carrier = make_carrier_group(gui, undo, "Route Target", [BLACKHOLE["route"]])
    gui.set_outbound_route("", carrier, name("313 to 1888"), prefix="1888", from_prefix="313")
    undo.push("delete outbound route 313->1888", lambda: _delete_route_named(gui, name("313 to 1888")))
    phone = make_softphone(gui, undo, "3134470158")  # From user starts with 313
    gui.reload_kamailio()

    # matching call: dSIPRouter must send it to the route's carrier
    with qa.capture([BLACKHOLE["route"]]) as cap:
        hit = softphone_call(cfg.host, phone["user"], phone["password"], phone["domain"], matching,
                             os.path.join(outdir, "match"), expect_answer=False, local_port=cfg.sipp_port)
    sent = [i for i in cap.invites() if i["dst"] == BLACKHOLE["route"]]
    assert hit["register_status"] == 200, hit["reason"]
    assert sent, "matching call was not sent to the route's carrier (final: {})".format(hit["invite_final"])
    assert hit["invite_final"] != NO_RULES

    # non-matching call: no rule, explicit 500
    miss = softphone_call(cfg.host, phone["user"], phone["password"], phone["domain"], non_matching,
                          os.path.join(outdir, "nomatch"), expect_answer=False, local_port=cfg.sipp_port)
    assert miss["invite_final"] == NO_RULES, "expected '{}', got {}".format(NO_RULES, miss["invite_final"])


@pytest.mark.case("ROUTE-009")
def test_route_009_no_default_outbound_route(cfg, gui, undo, outdir):
    """With the Default Outbound Route deleted, a call fails with "500 No rules defined for number"."""
    clash = outbound_routes_matching(gui, cfg.dial_number)
    assert not clash, "existing outbound routes match {}: {}".format(cfg.dial_number, clash)
    remove_default_outbound_route(gui, undo)
    phone = make_softphone(gui, undo, "3134470158")
    gui.reload_kamailio()

    r = softphone_call(cfg.host, phone["user"], phone["password"], phone["domain"], cfg.dial_number,
                       os.path.join(outdir, "sipp"), expect_answer=False, local_port=cfg.sipp_port)
    assert r["register_status"] == 200, r["reason"]
    assert r["invite_final"] == NO_RULES, "expected '{}', got {}".format(NO_RULES, r["invite_final"])


def _delete_route_named(gui, route_name):
    for r in gui.outbound_routes():
        if r.get("name") == route_name:
            gui.delete_outbound_route(r["ruleid"])
