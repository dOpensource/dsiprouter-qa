"""SSH access to the QA box: run commands and capture outgoing SIP.

The routing tests send calls to unrouted TEST-NET addresses, so the only way to see
what dSIPRouter did is on the QA box itself. Capture.invites() is the scripted
equivalent of "watch it in sngrep": it returns every INVITE Kamailio sent to the
watched addresses, with Call-ID and request URI.
"""
import re
import shlex
import subprocess
import time
import uuid


class SshError(RuntimeError):
    pass


class QaHost:
    def __init__(self, host, user="root", key=None, connect_timeout=15):
        self.host = host
        self.user = user
        self.key = key
        self.connect_timeout = connect_timeout

    def _ssh_cmd(self):
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout={}".format(self.connect_timeout),
               "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=15"]
        if self.key:
            cmd += ["-i", self.key, "-o", "IdentitiesOnly=yes"]
        return cmd + ["{}@{}".format(self.user, self.host)]

    def run(self, command, check=True, timeout=300, stdin=None):
        """Run a shell command on the QA box. Returns (exit code, stdout, stderr)."""
        p = subprocess.run(self._ssh_cmd() + [command], capture_output=True, text=True,
                           timeout=timeout, input=stdin)
        if p.returncode == 255:
            raise SshError("ssh to {} failed: {}".format(self.host, p.stderr.strip()[-300:]))
        if check and p.returncode != 0:
            raise SshError("`{}` exited {}: {}".format(command[:120], p.returncode,
                                                       (p.stderr or p.stdout).strip()[-500:]))
        return p.returncode, p.stdout, p.stderr

    def out(self, command, **kw):
        return self.run(command, **kw)[1].strip()

    def client_ip(self):
        """The public IP this runner connects from, as the QA box sees it."""
        ip = self.out("echo ${SSH_CLIENT%% *}")
        if not re.match(r"^[0-9a-fA-F:.]+$", ip):
            raise SshError("could not work out the runner's public IP (got {!r})".format(ip))
        return ip

    def capture(self, hosts):
        return Capture(self, hosts)

    def dispatcher_state(self, match="192.0.2."):
        """{destination URI: dispatcher FLAGS} from `kamcmd dispatcher.list`, for URIs containing `match`.
        FLAGS: A=active, I=inactive, D=disabled, P=probing, T=trying."""
        text = self.out("kamcmd dispatcher.list 2>&1", check=False)
        state = {}
        for m in re.finditer(r"URI:\s*(\S+)\s+FLAGS:\s*(\S+)", text):
            if match in m.group(1):
                state[m.group(1)] = m.group(2)
        return state

    def dsip_version(self):
        ver = self.out("grep -E '^VERSION' /etc/dsiprouter/gui/settings.py | cut -d= -f2 | tr -d \"' \"",
                       check=False)
        rev = self.out("git -C /opt/dsiprouter log -1 --format='%h %D'", check=False)
        return ver, rev


class Capture:
    """tcpdump on the QA box for UDP SIP sent to `hosts`, as a context manager."""

    def __init__(self, qa, hosts):
        self.qa = qa
        self.hosts = list(hosts)
        self.file = "/tmp/dsipqa-cap-{}.txt".format(uuid.uuid4().hex[:8])
        self.pid = None
        self.text = ""

    def __enter__(self):
        rc, _, _ = self.qa.run("command -v tcpdump", check=False)
        if rc != 0:
            raise SshError("tcpdump is not installed on the QA box (apt install tcpdump)")
        filt = "udp and ({})".format(" or ".join("dst host {}".format(h) for h in self.hosts))
        self.pid = self.qa.out(
            "nohup timeout 900 tcpdump -i any -nn -l -s0 -A {} > {} 2>/dev/null & echo $!".format(
                shlex.quote(filt), self.file))
        time.sleep(2)  # let tcpdump attach before traffic starts
        return self

    def __exit__(self, *exc):
        self.stop()
        return False

    def stop(self):
        if self.pid:
            time.sleep(1)
            self.qa.run("kill {} 2>/dev/null; sleep 1".format(int(self.pid)), check=False)
            self.text = self.qa.out("cat {} 2>/dev/null; rm -f {}".format(self.file, self.file), check=False)
            self.pid = None
        return self.text

    def invites(self):
        return parse_tcpdump_invites(self.text)


_HDR = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d+ .*?IP6? (\S+)\.(\d+) > (\S+)\.(\d+):", re.M)


def parse_tcpdump_invites(text):
    """INVITEs in `tcpdump -A` output, deduplicated (retransmissions), in send order.

    Returns [{"dst": ip, "call_id": str, "ruri": str, "ruri_user": str}].
    """
    seen, out = set(), []
    heads = list(_HDR.finditer(text))
    for i, h in enumerate(heads):
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        m = re.search(r"INVITE (sip:[^\s]+) SIP/2\.0", body)
        if not m:
            continue
        cid = re.search(r"^(?:Call-ID|i):\s*(\S+)", body, re.M | re.I)
        dst = h.group(3)
        key = (dst, cid.group(1) if cid else m.group(1))
        if key in seen:
            continue
        seen.add(key)
        ruri = m.group(1)
        user = re.match(r"sip:([^@;>]+)@", ruri)
        out.append({"dst": dst, "call_id": cid.group(1) if cid else "", "ruri": ruri,
                    "ruri_user": user.group(1) if user else ""})
    return out


def first_destination_per_call(invites):
    """{call_id: first dst} — where the load balancer sent each call first."""
    first = {}
    for inv in invites:
        first.setdefault(inv["call_id"], inv["dst"])
    return first
