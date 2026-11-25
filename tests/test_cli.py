"""Command line testing (CLI-001): the dsiprouter CLI on the QA box, over SSH.

These restart services and change the admin password, so they run last and restore
everything they touch: the original GUI password (from the Jenkins credential),
settings.py (from a backup), and the generated Kamailio/RTPEngine configs.
"""
import re
import time

import pytest

pytestmark = [pytest.mark.case("CLI-001"), pytest.mark.disruptive]

SETTINGS = "/etc/dsiprouter/gui/settings.py"
KAM_CFG = "/etc/kamailio/kamailio.cfg"
RTP_CFG = "/etc/rtpengine/rtpengine.conf"
HOMER_MARK = "192.0.2.99"  # unrouted test address, so nothing actually receives HEP
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def wait_for_gui(gui, want=200, timeout=90):
    end, last = time.time() + timeout, None
    while time.time() < end:
        last = gui.status("/")
        if last == want:
            return last
        time.sleep(3)
    return last


def test_cli_resetpassword(cfg, gui, qa, undo):
    """`dsiprouter resetpassword`: the new password works immediately and the old one doesn't."""
    original = gui.password

    def restore():
        qa.run('read -r P; dsiprouter setcredentials -dc "{}:$P" >/dev/null'.format(cfg.gui_user),
               stdin=original + "\n")
        if not gui.can_login(original):
            qa.run("dsiprouter restart >/dev/null 2>&1", check=False)
            wait_for_gui(gui)
            if not gui.can_login(original):
                raise AssertionError("original GUI password does not work after setcredentials")
        gui.login(original)
    undo.push("restore GUI password (setcredentials)", restore)

    out = ANSI.sub("", qa.out("dsiprouter resetpassword"))
    m = re.search(r"dSIPRouter GUI Password:\s*(\S+)", out)
    assert m, "resetpassword did not print a new GUI password"
    new = m.group(1)
    assert new != original
    assert gui.can_login(new), "new password from resetpassword does not work"
    assert not gui.can_login(original), "old password still works after resetpassword"


def _homer_roundtrip(qa, undo, command, generated_file):
    qa.run("cp -p {0} {0}.qa-bak".format(SETTINGS))

    def restore():
        qa.run("mv -f {0}.qa-bak {0}".format(SETTINGS))
        qa.run("dsiprouter configurekam >/dev/null && dsiprouter configurertp >/dev/null", timeout=600)
        rc, _, _ = qa.run("grep -q {} {} {}".format(HOMER_MARK, KAM_CFG, RTP_CFG), check=False)
        if rc == 0:
            raise AssertionError("test Homer host still in generated configs after restore")
    undo.push("restore settings.py and regenerate configs", restore)

    qa.run("sed -i \"s/^HOMER_HEP_HOST\\s*=.*/HOMER_HEP_HOST = '{}'/\" {}".format(HOMER_MARK, SETTINGS))
    assert qa.out("grep '^HOMER_HEP_HOST' {}".format(SETTINGS)).endswith("'{}'".format(HOMER_MARK))
    qa.run("dsiprouter {} >/dev/null".format(command), timeout=600)
    rc, _, _ = qa.run("grep -q {} {}".format(HOMER_MARK, generated_file), check=False)
    assert rc == 0, "{} did not write the new Homer host into {}".format(command, generated_file)


def test_cli_configurekam(qa, undo):
    """`dsiprouter configurekam` regenerates kamailio.cfg from settings.py (Homer host change)."""
    _homer_roundtrip(qa, undo, "configurekam", KAM_CFG)


def test_cli_configurertp(qa, undo):
    """`dsiprouter configurertp` regenerates rtpengine.conf from settings.py (Homer host change)."""
    _homer_roundtrip(qa, undo, "configurertp", RTP_CFG)


def test_cli_stop_start_restart(gui, qa, undo):
    """stop -> GUI answers 502 Bad Gateway; start -> GUI back; restart -> GUI back."""
    def ensure_started():
        if gui.status("/") != 200:
            qa.run("dsiprouter start >/dev/null 2>&1", check=False, timeout=600)
            if wait_for_gui(gui) != 200:
                raise AssertionError("GUI not reachable after dsiprouter start")
        gui.login()
    undo.push("make sure dSIPRouter is running", ensure_started)

    qa.run("dsiprouter stop", timeout=600)
    got = wait_for_gui(gui, want=502, timeout=30)
    assert got == 502, "after stop, expected nginx 502 Bad Gateway, got {}".format(
        "no connection" if got == -1 else got)

    qa.run("dsiprouter start", timeout=600)
    assert wait_for_gui(gui) == 200, "GUI did not come back after dsiprouter start"

    qa.run("dsiprouter restart", timeout=600)
    assert wait_for_gui(gui) == 200, "GUI did not come back after dsiprouter restart"
    gui.login()
