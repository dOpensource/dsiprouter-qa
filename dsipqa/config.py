"""Run configuration, read from environment variables (Jenkins sets these from credentials).

| Variable              | Meaning                                                     | Default             |
|-----------------------|-------------------------------------------------------------|---------------------|
| DSIP_HOST             | QA box: GUI host, SIP proxy and SSH target                  | qa.dsiprouter.net   |
| DSIP_GUI_PORT         | GUI port                                                    | 5000                |
| DSIP_TLS_INSECURE     | 1 = don't verify the GUI certificate (needed when DSIP_HOST is an IP) | 0         |
| DSIP_GUI_USER         | GUI user                                                    | admin               |
| DSIP_QA_PASSWORD      | GUI password (falls back to macOS keychain item dsiprouter-qa) |                  |
| DSIP_SSH_USER         | SSH user on the QA box                                      | root                |
| DSIP_SSH_KEY          | SSH private key file                                        | ~/.ssh/jump         |
| QA_AGENT_IP           | public IP this runner's SIP traffic comes from (auto-detected over SSH) |         |
| SIPP_LOCAL_PORT       | local UDP port for SIPp                                     | 5060                |
| QA_DIAL_NUMBER        | PSTN number for real outbound calls                         | +18889072085        |
| FLOWROUTE_HOST        | Flowroute SIP host                                          | us-east-va.sip.flowroute.com |
| FLOWROUTE_TECH_PREFIX | Flowroute tech prefix                                       | 16723617*           |
| SIGNALWIRE_HOST       | SignalWire SIP domain                                       | msteamsdev-b9c54f5e9bd6.sip.signalwire.com |
| SIGNALWIRE_USER / SIGNALWIRE_PASS | SignalWire SIP credentials (ROUTE-002 skips without them) |      |
| QA_OUTDIR             | where traces and results.json go                            | ./results           |
"""
import os


def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


class Config:
    def __init__(self):
        self.host = env("DSIP_HOST", "qa.dsiprouter.net")
        self.gui_port = int(env("DSIP_GUI_PORT", "5000"))
        self.tls_insecure = env("DSIP_TLS_INSECURE", "0") == "1"
        self.gui_user = env("DSIP_GUI_USER", "admin")
        self.gui_password = env("DSIP_QA_PASSWORD")
        self.ssh_user = env("DSIP_SSH_USER", "root")
        self.ssh_key = os.path.expanduser(env("DSIP_SSH_KEY", "~/.ssh/jump"))
        self.agent_ip = env("QA_AGENT_IP")
        self.sipp_port = int(env("SIPP_LOCAL_PORT", "5060"))
        self.dial_number = env("QA_DIAL_NUMBER", "+18889072085")
        self.flowroute_host = env("FLOWROUTE_HOST", "us-east-va.sip.flowroute.com")
        self.flowroute_prefix = env("FLOWROUTE_TECH_PREFIX", "16723617*")
        self.signalwire_host = env("SIGNALWIRE_HOST", "msteamsdev-b9c54f5e9bd6.sip.signalwire.com")
        self.signalwire_user = env("SIGNALWIRE_USER")
        self.signalwire_pass = env("SIGNALWIRE_PASS")
        self.outdir = os.path.abspath(env("QA_OUTDIR", "results"))


# Everything the tests create is named with this prefix, so leftovers from a crashed
# run can be recognised and removed without touching anyone else's objects.
PREFIX = "QA-AUTO"

# TEST-NET-1 addresses (RFC 5737): never routed, so INVITEs sent there get no answer.
# The QA plan's "blackhole IPs". Kamailio times out on them and fails over.
BLACKHOLE = {
    "ep1": "192.0.2.1",
    "ep2": "192.0.2.2",
    "ep3": "192.0.2.3",
    "carrier1": "192.0.2.11",
    "carrier2": "192.0.2.12",
    "route": "192.0.2.21",
}

# A DID that only exists for these tests (555-01xx is reserved for fiction).
TEST_DID = "15555550100"
TEST_FWD_DID = "15555550199"
