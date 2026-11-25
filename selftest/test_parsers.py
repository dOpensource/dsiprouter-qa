"""Offline checks of the parsing code (no QA box needed): `pytest selftest`."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from dsipqa.gui import parse_inbound_table  # noqa: E402
from dsipqa.qahost import first_destination_per_call, parse_tcpdump_invites  # noqa: E402
from dsipqa.sip import summarize  # noqa: E402
import sheet_sync  # noqa: E402

TCPDUMP = """\
02:10:01.000001 eth0  Out IP 167.99.236.184.5060 > 192.0.2.1.5060: SIP: INVITE sip:15555550199@192.0.2.1:5060 SIP/2.0
E....@.@...
INVITE sip:15555550199@192.0.2.1:5060 SIP/2.0
Via: SIP/2.0/UDP 167.99.236.184;branch=z9hG4bK1
Call-ID: abc-1@10.0.0.5
CSeq: 1 INVITE

02:10:01.500001 eth0  Out IP 167.99.236.184.5060 > 192.0.2.1.5060: SIP: INVITE sip:15555550199@192.0.2.1:5060 SIP/2.0
E....@.@...
INVITE sip:15555550199@192.0.2.1:5060 SIP/2.0
Call-ID: abc-1@10.0.0.5
CSeq: 1 INVITE

02:10:31.000001 eth0  Out IP 167.99.236.184.5060 > 192.0.2.2.5060: SIP: INVITE sip:15555550199@192.0.2.2:5060 SIP/2.0
E....@.@...
INVITE sip:15555550199@192.0.2.2:5060 SIP/2.0
Call-ID: abc-1@10.0.0.5
CSeq: 2 INVITE

02:10:32.000001 eth0  Out IP 167.99.236.184.5060 > 192.0.2.2.5060: SIP: INVITE sip:15555550100@192.0.2.2 SIP/2.0
E....
INVITE sip:15555550100@192.0.2.2 SIP/2.0
i: other-2
"""


def test_tcpdump_invites_dedup_and_order():
    inv = parse_tcpdump_invites(TCPDUMP)
    assert [(i["dst"], i["call_id"]) for i in inv] == [
        ("192.0.2.1", "abc-1@10.0.0.5"), ("192.0.2.2", "abc-1@10.0.0.5"), ("192.0.2.2", "other-2")]
    assert inv[0]["ruri_user"] == "15555550199"
    assert first_destination_per_call(inv) == {"abc-1@10.0.0.5": "192.0.2.1", "other-2": "192.0.2.2"}


def test_tcpdump_empty():
    assert parse_tcpdump_invites("") == []


INBOUND_HTML = """
<table><thead><tr class='element-row'><th data-field="ruleid">Rule ID</th></tr></thead>
<tbody>
      <tr class='element-row'>
        <td><input type="checkbox" class="checkthis" value="1"/></td>
        <td class='ruleid'>42</td>
        <td class='prefix'>15555550100</td>
        <td class="gwgroupid d-none">17</td>
        <td class='gwgroupname'>QA-AUTO HF Endpoints<sup> +1</sup></td>
        <td class="rulename">QA-AUTO Hard Forward</td>
        <td class="gwlist d-none">#17</td>
        <td class="lb_enabled d-none">0</td>
        <td><p><button id="open-Update"></button></p></td>
        <td class="hf_ruleid d-none">42</td>
        <td class="hf_groupid d-none">20001</td>
        <td class="hf_gwgroupid d-none">17</td>
        <td class="hf_fwddid d-none">15555550199</td>
        <td class="ff_ruleid d-none"></td>
        <td class="ff_groupid d-none"></td>
        <td class="ff_gwgroupid d-none"></td>
        <td class="ff_fwddid d-none"></td>
      </tr>
</tbody></table>"""


def test_inbound_table():
    rows = parse_inbound_table(INBOUND_HTML)
    assert len(rows) == 1
    r = rows[0]
    assert (r["ruleid"], r["prefix"], r["gwgroupid"], r["rulename"]) == ("42", "15555550100", "17", "QA-AUTO Hard Forward")
    assert (r["hf_groupid"], r["hf_fwddid"], r["ff_ruleid"]) == ("20001", "15555550199", "")


SIPP_TRACE = """\
----------------------------------------------- 2026-11-25 00:00:00.000
UDP message received [400] bytes :

SIP/2.0 401 Unauthorized
Call-ID: 1-123@10.0.0.9
CSeq: 1 REGISTER

----------------------------------------------- 2026-11-25 00:00:00.100
UDP message received [400] bytes :

SIP/2.0 200 OK
Call-ID: 1-123@10.0.0.9
CSeq: 2 REGISTER

----------------------------------------------- 2026-11-25 00:00:00.200
UDP message received [400] bytes :

SIP/2.0 407 Proxy Authentication Required
Call-ID: 1-123@10.0.0.9
CSeq: 3 INVITE

----------------------------------------------- 2026-11-25 00:00:00.300
UDP message received [400] bytes :

SIP/2.0 500 No rules defined for number
Call-ID: 1-123@10.0.0.9
CSeq: 4 INVITE

"""


def test_summarize_no_rules(tmp_path):
    t = tmp_path / "trace.log"
    t.write_text(SIPP_TRACE)
    r = summarize(str(t), str(tmp_path / "none.log"))
    assert r["register_status"] == 200
    assert r["invite_final"] == "500 No rules defined for number"
    assert not r["answered"]


def test_aggregate_per_case():
    agg = sheet_sync.aggregate([
        {"case": "CLI-001", "nodeid": "t::a", "outcome": "passed", "duration": 1},
        {"case": "CLI-001", "nodeid": "t::b", "outcome": "failed", "duration": 2, "details": "E boom"},
        {"case": "ROUTE-002", "nodeid": "t::c", "outcome": "skipped", "duration": 0, "details": "no creds"},
        {"case": "ROUTE-001", "nodeid": "t::d", "outcome": "passed", "duration": 3},
    ])
    assert agg["CLI-001"]["result"] == "FAIL" and agg["CLI-001"]["duration"] == 3
    assert "b: E boom" in agg["CLI-001"]["details"][0]
    assert agg["ROUTE-002"]["result"] == "SKIP"
    assert agg["ROUTE-001"]["result"] == "PASS"


def test_repo_cases_match_sheet_ids():
    tagged = sheet_sync.repo_cases()
    assert set(tagged) == {"ROUTE-001", "ROUTE-002", "ROUTE-003", "ROUTE-004", "ROUTE-005",
                           "ROUTE-006", "ROUTE-007", "ROUTE-008", "ROUTE-009", "CLI-001"}
    json.dumps(tagged)
