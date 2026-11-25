"""Reusable test steps: create QA-AUTO objects on the QA box and register their cleanup."""
from .config import PREFIX


def name(label):
    return "{} {}".format(PREFIX, label)


def make_softphone(gui, undo, user, label=None):
    """User/pass endpoint group for a SIPp softphone. Returns dict(user, password, domain, gwgroupid)."""
    password = gui.generate_password()
    gid = gui.create_userpwd_endpoint_group(name(label or "Softphone {}".format(user)), user, password)
    undo.push("delete endpoint group {}".format(gid), lambda: gui.delete_endpoint_group(gid))
    epg = gui.get_endpoint_group(gid)
    domain = (epg.get("auth") or {}).get("domain") or gui.base.split("//")[1].split(":")[0]
    return {"user": user, "password": password, "domain": domain, "gwgroupid": gid}


def make_carrier_group(gui, undo, label, hosts, prefix=""):
    """IP-auth carrier group with one carrier per host."""
    gid = gui.create_carrier_group(name(label), auth_type="ip")
    undo.push("delete carrier group {}".format(gid), lambda: gui.delete_carrier_group(gid))
    for i, h in enumerate(hosts, 1):
        gui.add_carrier(gid, "{} c{}".format(name(label), i), h, prefix=prefix)
    return gid


def make_ip_endpoint_group(gui, undo, label, hosts):
    gid = gui.create_ip_endpoint_group(name(label), hosts)
    undo.push("delete endpoint group {}".format(gid), lambda: gui.delete_endpoint_group(gid))
    return gid


def swap_default_outbound_route(gui, undo, gwgroupid):
    """Point the Default Outbound Route at gwgroupid; restore it afterwards."""
    orig = gui.default_outbound_route()
    if orig is None:
        raise AssertionError("QA has no Default Outbound Route (groupid 8000, empty prefix)")
    orig_gw = str(orig.get("gwgroupid")).lstrip("#")
    orig_name = orig.get("name") or "Default Outbound Route"
    gui.set_outbound_route(orig["ruleid"], gwgroupid, orig_name)
    undo.push("restore default outbound route -> {}".format(orig_gw),
              lambda: gui.set_outbound_route(orig["ruleid"], orig_gw, orig_name))
    now = gui.default_outbound_route()
    assert str(now.get("gwgroupid")).lstrip("#") == str(gwgroupid), "default route did not change"
    return orig


def remove_default_outbound_route(gui, undo):
    """Delete the Default Outbound Route; re-create it (same carrier group and name) afterwards."""
    orig = gui.default_outbound_route()
    if orig is None:
        return None
    orig_gw = str(orig.get("gwgroupid")).lstrip("#")
    orig_name = orig.get("name") or "Default Outbound Route"
    gui.delete_outbound_route(orig["ruleid"])

    def restore():
        gui.set_outbound_route("", orig_gw, orig_name)
        if gui.default_outbound_route() is None:
            raise AssertionError("default outbound route was not re-created")
    undo.push("re-create default outbound route -> {}".format(orig_gw), restore)
    return orig


def add_inbound_route(gui, undo, did, gwgroupid, label, **kw):
    if gui.find_inbound_route(did):
        raise AssertionError("an inbound route for test DID {} already exists on QA".format(did))
    gui.set_inbound_route(did, gwgroupid, name(label), **kw)
    row = gui.find_inbound_route(did)
    assert row, "inbound route for {} was not created".format(did)
    undo.push("delete inbound route {}".format(did),
              lambda: gui.delete_inbound_route(gui.find_inbound_route(did) or row))
    return row


def outbound_routes_matching(gui, number):
    """Existing outbound routes whose prefix would match `number` (excluding the default)."""
    n = number.lstrip("+")
    return [r for r in gui.outbound_routes()
            if r.get("prefix") and n.startswith(str(r["prefix"]).lstrip("+"))]
