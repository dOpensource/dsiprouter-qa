"""dSIPRouter GUI client for QA tests (stdlib only).

Drives dSIPRouter the way the web GUI does: logs in with a browser-style session
(CSRF token + cookie), then calls the same endpoints the GUI pages call:

  - carrier groups, endpoint groups, password generation, kamailio reload:
    JSON calls to /api/v1/... (what the GUI's JavaScript does)
  - carriers, outbound routes, inbound routes: HTML form posts to /carriers,
    /outboundroutes, /inboundmapping (what the GUI's modals submit)

A logged-in session bypasses the API's Core-license check, so this works on a QA box
with or without a license. Don't replace GUI calls with SQL: the tests exist to
exercise the application logic behind these endpoints.
"""
import html as htmllib
import http.cookiejar
import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request

FLT_OUTBOUND = 8000  # dr_rules groupid for simple outbound routes
FLT_INBOUND = 9000   # dr_rules groupid for inbound DID routes


class DsipError(RuntimeError):
    pass


def keychain_password(service, account):
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


class DsipGui:
    def __init__(self, host, username="admin", password=None, port=5000, insecure=False):
        self.base = "https://{}:{}".format(host, port)
        self.username = username
        self.password = (password or os.environ.get("DSIP_QA_PASSWORD")
                         or keychain_password("dsiprouter-qa", username))
        if not self.password:
            raise DsipError("No GUI password: set DSIP_QA_PASSWORD (Jenkins credential) or the "
                            "macOS keychain item dsiprouter-qa")
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self.ctx = ctx
        self._new_session()

    def _new_session(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.ctx),
            urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = None

    # -- transport ---------------------------------------------------------
    def _request(self, method, path, body=None, headers=None, timeout=60):
        # Flask-WTF's strict HTTPS CSRF check rejects posts without a same-origin Referer
        headers = dict({"Referer": self.base + "/", "Origin": self.base}, **(headers or {}))
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def status(self, path="/", timeout=15):
        """HTTP status of a GET (no exception on 4xx/5xx, -1 if unreachable)."""
        try:
            return self._request("GET", path, headers={"Accept": "text/html"}, timeout=timeout)[0]
        except (urllib.error.URLError, OSError):
            return -1

    def api(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        status, text = self._request(method, "/api/v1/" + path.lstrip("/"), body, {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        })
        try:
            data = json.loads(text)
        except ValueError:
            raise DsipError("{} {} -> HTTP {} (non-JSON): {}".format(method, path, status, text[:300]))
        if status >= 400 or data.get("error"):
            raise DsipError("{} {} -> HTTP {}: {}".format(method, path, status, data.get("msg") or data))
        return data

    def form(self, path, fields):
        fields = dict(fields, csrf_token=self._csrf_token())
        status, text = self._request("POST", path, urllib.parse.urlencode(fields).encode(), {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
        })
        if status >= 400:
            raise DsipError("POST {} -> HTTP {}: {}".format(path, status, _html_error(text)))
        err = _html_error(text, only_if_error=True)
        if err:
            raise DsipError("POST {} -> GUI error: {}".format(path, err))
        return text

    def page(self, path):
        status, text = self._request("GET", path, headers={"Accept": "text/html"})
        if status != 200:
            raise DsipError("GET {} -> HTTP {}".format(path, status))
        return text

    def _csrf_token(self, page="/"):
        if self.csrf is None:
            _, html = self._request("GET", page, headers={"Accept": "text/html"})
            m = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
            if not m:
                raise DsipError("Could not find a CSRF token on {}".format(page))
            self.csrf = m.group(1)
        return self.csrf

    # -- session -----------------------------------------------------------
    def login(self, password=None):
        """Log in (optionally with a different password). Returns self; raises on failure."""
        self._new_session()
        token = self._csrf_token("/")
        self._request("POST", "/login", urllib.parse.urlencode({
            "username": self.username, "password": password or self.password,
            "csrf_token": token, "nextpage": "/"}).encode(),
            {"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/html"})
        # the login page always redirects; confirm by hitting a session-gated endpoint
        status, _ = self._request("GET", "/api/v1/endpointgroups", headers={"Accept": "application/json"})
        if status != 200:
            raise DsipError("GUI login failed for user {!r}".format(self.username))
        self.csrf = None  # refresh the token for the logged-in session
        return self

    def can_login(self, password):
        try:
            self.login(password)
            return True
        except DsipError:
            return False

    # -- carrier groups ----------------------------------------------------
    def carrier_groups(self):
        return self.api("GET", "carriergroups")["data"]

    def create_carrier_group(self, name, auth_type="ip", strip="", prefix="", **auth):
        """auth_type 'ip' or 'userpwd'; userpwd takes r_username, auth_username,
        auth_password, auth_domain, auth_proxy."""
        payload = {"name": name, "lb_enabled": "0", "strip": strip, "prefix": prefix,
                   "auth": dict({"type": auth_type, "r_username": "", "auth_username": "",
                                 "auth_password": "", "auth_domain": "", "auth_proxy": ""}, **auth)}
        return int(self.api("POST", "carriergroups", payload)["data"][0]["gwgroupid"])

    def add_carrier(self, gwgroupid, name, host, prefix="", strip="", rweight="1"):
        self.form("/carriers", {"gwgroup": gwgroupid, "gwid": "", "name": name, "ip_addr": host,
                                "strip": strip, "prefix": prefix, "rweight": rweight})

    def delete_carrier_group(self, gwgroupid):
        return self.api("DELETE", "carriergroups/{}".format(gwgroupid))

    # -- endpoint groups ---------------------------------------------------
    def endpoint_groups(self):
        return self.api("GET", "endpointgroups")["data"]

    def get_endpoint_group(self, gwgroupid):
        return self.api("GET", "endpointgroups/{}".format(gwgroupid))["data"][0]

    def generate_password(self):
        return self.api("GET", "sys/generatepassword")["data"][0]

    def _endpoint_group_payload(self, name, auth, endpoints):
        return {
            "name": name,
            "call_settings": {"limit": None, "timeout": None},
            "auth": auth,
            "strip": 0, "did_override": "", "prefix": "",
            "notifications": {"overmaxcalllimit": "", "endpointfailure": ""},
            "cdr": {"cdr_email": "", "cdr_send_interval": "    "},
            "fusionpbx": {"enabled": "0", "dbhost": "", "dbuser": "", "dbpass": "", "clustersupport": "0"},
            "endpoints": endpoints,
        }

    def create_userpwd_endpoint_group(self, name, user, password, domain=""):
        payload = self._endpoint_group_payload(
            name, {"type": "userpwd", "user": user, "pass": password, "domain": domain}, [])
        return int(self.api("POST", "endpointgroups", payload)["data"][0]["gwgroupid"])

    def create_ip_endpoint_group(self, name, hosts):
        """IP-auth endpoint group; hosts is a list of 'host' or 'host:port' strings."""
        endpoints = []
        for i, h in enumerate(hosts, 1):
            host, _, port = h.partition(":")
            endpoints.append({"host": host, "port": int(port or 5060), "signalling": "proxy",
                              "media": "proxy", "description": "{} ep{}".format(name, i),
                              "rweight": 1, "keepalive": 0})
        payload = self._endpoint_group_payload(
            name, {"type": "ip", "user": "", "pass": "", "domain": ""}, endpoints)
        return int(self.api("POST", "endpointgroups", payload)["data"][0]["gwgroupid"])

    def delete_endpoint_group(self, gwgroupid):
        return self.api("DELETE", "endpointgroups/{}".format(gwgroupid))

    # -- outbound routes ---------------------------------------------------
    def outbound_routes(self):
        return self.api("GET", "outboundroutes")["data"]

    def default_outbound_route(self):
        """The GUI's 'Default Outbound Route': simple route (groupid 8000) with no prefixes."""
        for r in self.outbound_routes():
            if str(r.get("groupid")) == str(FLT_OUTBOUND) and not r.get("prefix") and not r.get("from_prefix"):
                return r
        return None

    def set_outbound_route(self, ruleid, gwgroupid, name, prefix="", from_prefix="", priority=0,
                           groupid=FLT_OUTBOUND):
        """Add (ruleid='') or update an outbound route, as the Outbound Routes modal does."""
        self.form("/outboundroutes", {
            "ruleid": ruleid, "groupid": groupid if ruleid else "", "from_prefix": from_prefix,
            "prefix": prefix, "timerec": "", "priority": priority, "routeid": "",
            "gwgroupid": gwgroupid, "name": name})

    def delete_outbound_route(self, ruleid):
        self.form("/outboundroutesdelete", {"ruleid": ruleid})

    # -- inbound routes ----------------------------------------------------
    def inbound_routes(self):
        """Rows of the Inbound Routes page, including the hidden forwarding columns."""
        return parse_inbound_table(self.page("/inboundmapping"))

    def set_inbound_route(self, did, gwgroupid, name, ruleid="", lb=False,
                          hardfwd=None, failfwd=None, current=None):
        """Add (ruleid='') or update an inbound route as the Inbound Routes modal does.

        hardfwd / failfwd: None (disabled) or {"gwgroupid": str|'' , "did": str|''}.
        current: the existing row from inbound_routes() when updating (supplies
        hf_groupid / ff_groupid, which the GUI posts back from hidden fields).
        """
        current = current or {}
        hardfwd = hardfwd or {}
        failfwd = failfwd or {}
        self.form("/inboundmapping", {
            "ruleid": ruleid,
            "prefix": did,
            "rulename": name,
            "gwgroupid": "lb_{}".format(gwgroupid) if lb else str(gwgroupid),
            "hardfwd_enabled": "1" if hardfwd else "0",
            "hf_gwgroupid": str(hardfwd.get("gwgroupid", "")),
            "hf_groupid": current.get("hf_groupid", ""),
            "hf_fwddid": hardfwd.get("did", ""),
            "failfwd_enabled": "1" if failfwd else "0",
            "ff_gwgroupid": str(failfwd.get("gwgroupid", "")),
            "ff_groupid": current.get("ff_groupid", ""),
            "ff_fwddid": failfwd.get("did", ""),
        })

    def find_inbound_route(self, did):
        rows = [r for r in self.inbound_routes() if r.get("prefix") == did]
        return rows[0] if rows else None

    def delete_inbound_route(self, row):
        self.form("/inboundmappingdelete", {
            "ruleid": row["ruleid"],
            "hf_ruleid": row.get("hf_ruleid", ""), "hf_groupid": row.get("hf_groupid", ""),
            "ff_ruleid": row.get("ff_ruleid", ""), "ff_groupid": row.get("ff_groupid", ""),
        })

    # -- kamailio ----------------------------------------------------------
    def reload_kamailio(self):
        return self.api("POST", "reload/kamailio")


INBOUND_FIELDS = ("ruleid", "prefix", "gwgroupid", "rulename", "gwlist", "lb_enabled",
                  "hf_ruleid", "hf_groupid", "hf_gwgroupid", "hf_fwddid",
                  "ff_ruleid", "ff_groupid", "ff_gwgroupid", "ff_fwddid")


def parse_inbound_table(html):
    """Parse inboundmapping.html's <tr class='element-row'> rows into dicts."""
    rows = []
    tbody = html.split("<tbody", 1)[-1]
    for tr in re.findall(r"<tr class=['\"]element-row['\"]>(.*?)</tr>", tbody, re.S):
        row = {}
        for field in INBOUND_FIELDS:
            m = re.search(r"<td class=['\"]{}(?: d-none)?['\"]>(.*?)</td>".format(field), tr, re.S)
            if m:
                row[field] = htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
        if row.get("ruleid"):
            rows.append(row)
    return rows


def _html_error(html, only_if_error=False):
    m = re.search(r'class="[^"]*(?:alert-danger|error)[^"]*"[^>]*>\s*(.*?)\s*<', html, re.S)
    if m and m.group(1).strip():
        return re.sub(r"\s+", " ", m.group(1)).strip()[:300]
    return None if only_if_error else re.sub(r"<[^>]+>|\s+", " ", html)[:300]
