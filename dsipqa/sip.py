"""SIPp wrappers: a registering softphone and an IP-trusted "carrier".

Both return a dict summarising what the far side (dSIPRouter) answered:
  register_status   final REGISTER code (softphone only)
  invite_finals     list of "code reason" strings, one per call
  answered          True if any INVITE got 200
  bye_status        final BYE code (if a call was answered)
  trace             path to the SIP message trace
"""
import os
import re
import shutil
import subprocess

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS = os.path.join(HERE, "sipp")


def _sipp(cmd, outdir, secret=None):
    if not shutil.which("sipp"):
        raise RuntimeError("sipp is not installed (apt install sip-tester / brew install sipp)")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=outdir)
    shown = " ".join("***" if secret and c == secret else c for c in cmd)
    return proc, shown


def _trace_paths(outdir):
    os.makedirs(outdir, exist_ok=True)
    trace = os.path.join(outdir, "sip_messages.log")
    errlog = os.path.join(outdir, "sipp_errors.log")
    for p in (trace, errlog):
        if os.path.exists(p):
            os.remove(p)
    return trace, errlog


def softphone_call(proxy, user, password, domain, number, outdir, hold_s=10,
                   expect_answer=True, port=5060, local_port=5060, timeout_s=None):
    """Register user/password and call number through proxy.

    expect_answer=True uses register_call.xml (must be answered); False uses
    register_attempt.xml, which accepts any final response (for routing tests that
    check where the call went, or that it was refused)."""
    trace, errlog = _trace_paths(outdir)
    inf = os.path.join(outdir, "creds.csv")
    with open(inf, "w") as f:
        f.write("SEQUENTIAL\n{};-;{}\n".format(user, domain))
    os.chmod(inf, 0o600)
    scenario = "register_call.xml" if expect_answer else "register_attempt.xml"
    timeout_s = timeout_s or hold_s + 150
    cmd = ["sipp", "{}:{}".format(proxy, port), "-sf", os.path.join(SCENARIOS, scenario),
           "-inf", inf, "-s", number, "-au", user, "-ap", password,
           "-m", "1", "-l", "1", "-d", str(hold_s * 1000), "-p", str(local_port),
           "-t", "u1", "-nostdin", "-nd",
           "-timeout", "{}s".format(timeout_s), "-timeout_error",
           "-recv_timeout", "140000",
           "-trace_msg", "-message_file", trace, "-trace_err", "-error_file", errlog]
    try:
        proc, shown = _sipp(cmd, outdir, secret=password)
    finally:
        os.remove(inf)  # don't leave the generated password on disk
    r = summarize(trace, errlog)
    r.update(sipp_exit=proc.returncode, trace=trace, command=shown)
    if expect_answer:
        r["passed"] = proc.returncode == 0 and r["answered"] and r["bye_status"] == 200
        if not r["passed"] and not r.get("reason"):
            r["reason"] = (proc.stderr.strip().splitlines() or ["SIPp exit {}".format(proc.returncode)])[-1]
    return r


def carrier_call(proxy, did, outdir, calls=1, rate=1, hold_s=5, port=5060, local_port=5060,
                 timeout_s=None):
    """Send `calls` unauthenticated INVITEs for `did` from this host (the test carrier)."""
    trace, errlog = _trace_paths(outdir)
    timeout_s = timeout_s or hold_s + 150 + calls * 2
    cmd = ["sipp", "{}:{}".format(proxy, port), "-sf", os.path.join(SCENARIOS, "carrier_invite.xml"),
           "-s", did, "-m", str(calls), "-l", str(calls), "-r", str(rate), "-rp", "1000",
           "-d", str(hold_s * 1000), "-p", str(local_port),
           "-t", "u1", "-nostdin", "-nd",
           "-timeout", "{}s".format(timeout_s), "-timeout_error",
           "-recv_timeout", "140000",
           "-trace_msg", "-message_file", trace, "-trace_err", "-error_file", errlog]
    proc, shown = _sipp(cmd, outdir)
    r = summarize(trace, errlog)
    r.update(sipp_exit=proc.returncode, trace=trace, command=shown)
    return r


def summarize(trace, errlog):
    text = open(trace, errors="replace").read() if os.path.exists(trace) else ""
    # (call-id, method, code, reason) for every response SIPp received
    responses = []
    for block in re.split(r"\n-{10,}.*\n", text):
        m = re.search(r"^SIP/2\.0 (\d{3}) ?(.*)$", block, re.M)
        c = re.search(r"^CSeq:\s*\d+\s+(\w+)", block, re.M | re.I)
        cid = re.search(r"^(?:Call-ID|i):\s*(\S+)", block, re.M | re.I)
        if m and c:
            responses.append((cid.group(1) if cid else "", c.group(1).upper(),
                              int(m.group(1)), m.group(2).strip()))

    def finals(method):
        return [(cid, code, reason) for cid, meth, code, reason in responses
                if meth == method and code >= 200]

    reg = finals("REGISTER")
    inv = finals("INVITE")
    bye = finals("BYE")
    # per call, the last final INVITE response (the first may be a 401/407 challenge)
    per_call = {}
    for cid, code, reason in inv:
        per_call[cid] = "{} {}".format(code, reason).strip()
    out = {
        "register_status": reg[-1][1] if reg else None,
        "invite_finals": list(per_call.values()),
        "invite_final": list(per_call.values())[-1] if per_call else None,
        "answered": any(code == 200 for _, code, _ in inv),
        "bye_status": bye[-1][1] if bye else None,
        "reason": None,
    }
    if reg and out["register_status"] != 200:
        out["reason"] = "REGISTER failed: {} {}".format(reg[-1][1], reg[-1][2])
    elif out["invite_final"] is None:
        out["reason"] = "No final response to INVITE"
    elif not out["answered"]:
        out["reason"] = "Call not answered: INVITE got {}".format(out["invite_final"])
    elif out["bye_status"] != 200:
        out["reason"] = "Call answered but BYE got {}".format(out["bye_status"] or "no response")
    if os.path.exists(errlog) and os.path.getsize(errlog) and out["reason"]:
        last = open(errlog, errors="replace").read().strip().splitlines()[-1:]
        out["sipp_error"] = last[0][:300] if last else None
    return out
