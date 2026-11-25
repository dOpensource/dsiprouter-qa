"""Failover inside a group: when the first destination doesn't answer, dSIPRouter tries the next.

Destinations are unrouted TEST-NET addresses (RFC 5737), so every attempt times out
and the call fails over. A packet capture on the QA box shows which addresses got an
INVITE, which is what the plan's "validate with sngrep" step checks by eye.
"""
import os

import pytest

from dsipqa.config import BLACKHOLE, TEST_DID
from dsipqa.sip import carrier_call, softphone_call
from dsipqa.steps import (add_inbound_route, make_carrier_group, make_ip_endpoint_group,
                          make_softphone, swap_default_outbound_route)


@pytest.mark.case("ROUTE-004")
def test_route_004_carrier_failover(cfg, gui, qa, undo, outdir):
    """A carrier group with two carriers: an outbound call tries both.

    The plan uses p1/p2.detroitpbx.com as the two carriers. Those are live production
    proxies, so this test uses two unrouted TEST-NET addresses instead; they behave like
    the "blackhole IPs" the plan describes."""
    c1, c2 = BLACKHOLE["carrier1"], BLACKHOLE["carrier2"]
    carrier = make_carrier_group(gui, undo, "Failover Carriers", [c1, c2])
    swap_default_outbound_route(gui, undo, carrier)
    phone = make_softphone(gui, undo, "3134470158")
    gui.reload_kamailio()

    with qa.capture([c1, c2]) as cap:
        r = softphone_call(cfg.host, phone["user"], phone["password"], phone["domain"], cfg.dial_number,
                           os.path.join(outdir, "sipp"), expect_answer=False, local_port=cfg.sipp_port)
    assert r["register_status"] == 200, r["reason"]
    tried = [i["dst"] for i in cap.invites()]
    assert set(tried) == {c1, c2}, "expected INVITEs to both carriers, saw {} (final: {})".format(
        tried or "none", r["invite_final"])


@pytest.mark.case("ROUTE-005")
def test_route_005_endpoint_failover(cfg, gui, qa, undo, outdir, agent_ip):
    """An endpoint group with two endpoints: an inbound call from a carrier tries both.

    The plan points the default inbound route at the group. This test adds a route for
    its own test DID instead, which goes through the same routing code without
    changing QA's default inbound route."""
    e1, e2 = BLACKHOLE["ep1"], BLACKHOLE["ep2"]
    epg = make_ip_endpoint_group(gui, undo, "Failover Endpoints", [e1, e2])
    make_carrier_group(gui, undo, "Inbound Carrier", [agent_ip])
    add_inbound_route(gui, undo, TEST_DID, epg, "Endpoint Failover")
    gui.reload_kamailio()

    with qa.capture([e1, e2]) as cap:
        r = carrier_call(cfg.host, TEST_DID, os.path.join(outdir, "sipp"), local_port=cfg.sipp_port)
    tried = [i["dst"] for i in cap.invites()]
    assert r["invite_final"], "no final response from dSIPRouter ({})".format(r.get("sipp_error"))
    assert not r["invite_final"].startswith(("403", "404")), \
        "dSIPRouter refused the carrier call: {} (is {} the runner's public IP?)".format(r["invite_final"], agent_ip)
    assert set(tried) == {e1, e2}, "expected INVITEs to both endpoints, saw {} (final: {})".format(
        tried or "none", r["invite_final"])
