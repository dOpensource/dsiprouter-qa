"""Inbound routes: failover forwarding, hard forwarding and load balancing.

The runner acts as the carrier (its public IP goes in an IP-auth carrier group) and
sends INVITEs for a test DID. Endpoints are unrouted TEST-NET addresses; a packet
capture on the QA box shows where dSIPRouter sent each call and with which DID.
"""
import collections
import os

import pytest

from dsipqa.config import BLACKHOLE, TEST_DID, TEST_FWD_DID
from dsipqa.qahost import first_destination_per_call
from dsipqa.sip import carrier_call
from dsipqa.steps import add_inbound_route, make_carrier_group, make_ip_endpoint_group, name


def _check_accepted(r, agent_ip):
    assert r["invite_final"], "no final response from dSIPRouter ({})".format(r.get("sipp_error"))
    assert not r["invite_final"].startswith(("403", "404")), \
        "dSIPRouter refused the carrier call: {} (is {} the runner's public IP?)".format(r["invite_final"], agent_ip)


@pytest.mark.case("ROUTE-006")
def test_route_006_inbound_failover_forwarding(cfg, gui, qa, undo, outdir, agent_ip):
    """Inbound route to endpoint group A with failover forwarding to endpoint group B:
    when A doesn't answer, the call goes to B."""
    a, b = BLACKHOLE["ep1"], BLACKHOLE["ep2"]
    group_a = make_ip_endpoint_group(gui, undo, "FF Primary", [a])
    group_b = make_ip_endpoint_group(gui, undo, "FF Failover", [b])
    make_carrier_group(gui, undo, "Inbound Carrier", [agent_ip])
    add_inbound_route(gui, undo, TEST_DID, group_a, "Failover Forward",
                      failfwd={"gwgroupid": group_b, "did": ""})
    gui.reload_kamailio()

    with qa.capture([a, b]) as cap:
        r = carrier_call(cfg.host, TEST_DID, os.path.join(outdir, "sipp"), local_port=cfg.sipp_port)
    _check_accepted(r, agent_ip)
    tried = [i["dst"] for i in cap.invites()]
    assert tried[:1] == [a], "call should go to the primary group first, saw {}".format(tried or "none")
    assert b in tried, "call did not fail over to the second endpoint group, saw {}".format(tried)


@pytest.mark.case("ROUTE-007")
def test_route_007_inbound_hard_forwarding(cfg, gui, qa, undo, outdir, agent_ip):
    """Hard forwarding, two ways.

    Part 1: route TEST_DID to an endpoint group (192.0.2.1, .2) with hard forwarding to a
    forwarded DID in the same group. The call goes to 192.0.2.1 with the forwarded DID.
    Part 2: edit the route to hard-forward to a carrier group (192.0.2.3) with no
    forwarded DID. The call goes to 192.0.2.3 with the dialed DID unchanged.

    The plan's wording doesn't say which group part 1 forwards to; using the route's own
    endpoint group matches "you should see the call go to 192.0.2.1"."""
    e1, e2, c3 = BLACKHOLE["ep1"], BLACKHOLE["ep2"], BLACKHOLE["ep3"]
    epg = make_ip_endpoint_group(gui, undo, "HF Endpoints", [e1, e2])
    # created up front so cleanup deletes the route (which references it) before the group
    fwd_carrier = make_carrier_group(gui, undo, "HF Carrier", [c3])
    make_carrier_group(gui, undo, "Inbound Carrier", [agent_ip])
    add_inbound_route(gui, undo, TEST_DID, epg, "Hard Forward",
                      hardfwd={"gwgroupid": epg, "did": TEST_FWD_DID})
    gui.reload_kamailio()

    with qa.capture([e1, e2, c3]) as cap:
        r = carrier_call(cfg.host, TEST_DID, os.path.join(outdir, "part1"), local_port=cfg.sipp_port)
    _check_accepted(r, agent_ip)
    invs = cap.invites()
    assert invs and invs[0]["dst"] == e1, "part 1: expected first INVITE to {}, saw {}".format(
        e1, [i["dst"] for i in invs] or "none")
    assert invs[0]["ruri_user"] == TEST_FWD_DID, "part 1: forwarded DID not used on the second leg ({})".format(
        invs[0]["ruri"])

    # Part 2: forward to a carrier group, no forwarded number
    row = gui.find_inbound_route(TEST_DID)
    gui.set_inbound_route(TEST_DID, epg, name("Hard Forward"), ruleid=row["ruleid"],
                          hardfwd={"gwgroupid": fwd_carrier, "did": ""}, current=row)
    gui.reload_kamailio()

    with qa.capture([e1, e2, c3]) as cap:
        r = carrier_call(cfg.host, TEST_DID, os.path.join(outdir, "part2"), local_port=cfg.sipp_port)
    _check_accepted(r, agent_ip)
    invs = cap.invites()
    assert invs and invs[0]["dst"] == c3, "part 2: expected first INVITE to {}, saw {}".format(
        c3, [i["dst"] for i in invs] or "none")
    assert invs[0]["ruri_user"] == TEST_DID, "part 2: dialed DID changed on the second leg ({})".format(
        invs[0]["ruri"])


@pytest.mark.case("ROUTE-008")
def test_route_008_inbound_load_balancing(cfg, gui, qa, undo, outdir, agent_ip):
    """Inbound route with load balancing ("LB" group): 10 calls are spread across both endpoints.

    The plan notes the split only evens out over ~100 calls, so with 10 calls the test
    checks that each endpoint got at least 2 of the first INVITEs."""
    e1, e2 = BLACKHOLE["ep1"], BLACKHOLE["ep2"]
    epg = make_ip_endpoint_group(gui, undo, "LB Endpoints", [e1, e2])
    make_carrier_group(gui, undo, "Inbound Carrier", [agent_ip])
    add_inbound_route(gui, undo, TEST_DID, epg, "Load Balance", lb=True)
    gui.reload_kamailio()

    with qa.capture([e1, e2]) as cap:
        r = carrier_call(cfg.host, TEST_DID, os.path.join(outdir, "sipp"), calls=10, rate=5,
                         local_port=cfg.sipp_port)
    _check_accepted(r, agent_ip)
    first = first_destination_per_call(cap.invites())
    split = collections.Counter(first.values())
    assert len(first) >= 8, "only {} of 10 calls reached an endpoint ({})".format(len(first), dict(split))
    assert split[e1] >= 2 and split[e2] >= 2, "calls not spread across both endpoints: {}".format(dict(split))
