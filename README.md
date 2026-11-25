# dsiprouter-qa

Automated part of the dSIPRouter release QA plan
([dSIPRouter Test Plan](https://docs.google.com/spreadsheets/d/1oIHJG7mPBczIw56sr2qffSUKscZsVlx3xCgQEtrGGYE/edit)).
Jenkins runs it against the QA box for each release and writes a **Results &lt;release&gt;** tab back to the sheet.

## How the sheet and the code fit together

The plan tab has a header row and these columns:

| Column | Meaning |
|---|---|
| TestID | Stable ID per test case (ROUTE-001, CLI-001, ...). Code refers to cases only by this ID. |
| Test / Priority / Step | The plan as testers wrote it. |
| Automation | Automated, Partial, Planned or Manual. |
| Automated test | Which pytest test covers it. |

Each test is tagged with its TestID:

```python
@pytest.mark.case("ROUTE-003")
def test_route_003_global_outbound_routes(...):
```

- `tools/sheet_sync.py check` fails (Jenkins goes UNSTABLE) if a case is marked Automated but has no test, or a test's ID isn't in the sheet.
- `tools/sheet_sync.py publish` writes one row per TestID: PASS, FAIL or SKIP for automated cases.
  Other rows keep whatever a tester typed into Result and Notes, so manual results for the same release survive re-runs.

## What's automated so far

| TestID | Test | How it's checked |
|---|---|---|
| ROUTE-001 | Outbound via Flowroute (softphone user/pass, carrier IP auth) | Real PSTN call: REGISTER 200, INVITE 200, held 10 s, BYE 200 |
| ROUTE-002 | Outbound via SignalWire (carrier user/pass auth) | Real PSTN call; skipped until SignalWire credentials are set |
| ROUTE-003 | Global outbound routes (from 313 / to 1888) | Matching call is sent to the route's carrier (capture on QA); non-matching call gets `500 No rules defined for number` |
| ROUTE-004 | Carrier failover in a group | Both carriers get an INVITE |
| ROUTE-005 | Endpoint failover in a group | Both endpoints get an INVITE |
| ROUTE-006 | Inbound failover forwarding | Primary group first, then the failover group |
| ROUTE-007 | Inbound hard forwarding | Part 1: forwarded DID on the second leg. Part 2 (route edited): call goes to the forward carrier group with the DID unchanged |
| ROUTE-008 | Inbound load balancing | 10 calls; each endpoint gets at least 2 first INVITEs |
| ROUTE-009 | No default outbound route | `500 No rules defined for number` |
| CLI-001 | resetpassword, configurekam, configurertp, stop/start/restart | Over SSH; GUI status codes and generated config contents |

### How routing tests see where a call went

Destinations are TEST-NET addresses (192.0.2.x, RFC 5737), the plan's "blackhole IPs". Nothing answers them, so Kamailio times out and fails over.
While a call runs, the test runs `tcpdump` on the QA box and lists every INVITE sent to those addresses: destination, Call-ID and request URI.
That replaces "validate with sngrep". For inbound tests, the runner itself is the carrier: its public IP goes in an IP-auth carrier group and SIPp sends the INVITE.

### Where the tests differ from the sheet's wording

- **ROUTE-003, ROUTE-009:** the tests delete only the Default Outbound Route and re-create it afterwards, rather than "remove all outbound routes". First they check that no other existing route matches the test numbers.
- **ROUTE-004:** the sheet's carriers are p1/p2.detroitpbx.com. Those are live production proxies, so the test uses 192.0.2.11/12 instead.
- **ROUTE-005, ROUTE-008:** the tests add a route for the test DID 15555550100 instead of repointing QA's default inbound route.
- **ROUTE-007 part 1:** the sheet doesn't say which group to hard-forward to, so the test forwards to the route's own endpoint group, with forwarded DID 15555550199.
- **ROUTE-008:** the sheet expects an even split over about 100 calls. The test places 10 calls and checks that each endpoint gets at least 2.

## Safety rules the suite follows

- Everything it creates is named `QA-AUTO ...`. At the start of a run, it deletes leftovers with that prefix from crashed runs, and nothing else.
- Each change registers its undo, and the undos run in reverse after every test, pass or fail. A failed undo fails the test with `CLEANUP FAILED`.
- The CLI tests restore the original GUI password with `dsiprouter setcredentials`, put back `settings.py` from a backup, and regenerate the Kamailio and RTPEngine configs.
- Jenkins runs with `disableConcurrentBuilds()`, because the tests share QA's default outbound route.

## Running it

### Jenkins

The job is a Pipeline from SCM with this repo's `Jenkinsfile`. Before the first run:

1. On the agent: `apt install python3-venv sip-tester`. Its public IP must reach QA on UDP 5060 and SSH.
2. Jenkins credentials:

| ID | Type | What |
|---|---|---|
| `dsip-qa-gui` | Username with password | QA GUI admin login |
| `dsip-qa-ssh` | SSH username with private key | root on the QA box |
| `omnibot-google-sa` | Secret file | omnibot service-account JSON key; the sheet must be shared with it as an editor |

3. QA box prerequisites:
   - `tcpdump` is installed.
   - Its public IP is on the Flowroute test account's ACL with tech prefix `16723617` (plan step ROUTE-001.1).

Parameters:
- `RELEASE`: names the results tab. If blank, the version is read from the QA box.
- `RUN_PSTN`: turn off to skip the billed calls.
- `RUN_CLI`: turn off to skip the CLI tests, which restart services.
- `TLS_INSECURE`: needed while QA has a self-signed certificate.
- `PUBLISH_TO_SHEET`: turn off to skip writing results to the sheet.

### By hand

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export DSIP_HOST=qa.dsiprouter.net DSIP_QA_PASSWORD=... DSIP_SSH_KEY=~/.ssh/jump
.venv/bin/python -m pytest -v tests/test_outbound.py -k route_009     # one case
.venv/bin/python -m pytest -v -m "not pstn and not disruptive" tests  # everything free and non-disruptive
.venv/bin/python -m pytest selftest                                   # offline parser checks
```

Settings are environment variables, listed in `dsipqa/config.py`. Traces and `results.json` go to `results/<test>/`.
A runner behind NAT, such as a laptop, works for outbound tests. Inbound tests need the runner's public IP to receive SIP replies, which `;rport` usually handles.

## Adding a test case

1. Give the row a TestID in the sheet if it doesn't have one, and set Automation to **Automated**.
2. Write the test in `tests/`, tag it `@pytest.mark.case("<TestID>")`, and put the test path in the sheet's "Automated test" column.
3. Build objects with the helpers in `dsipqa/steps.py`, which register their own cleanup. Add GUI calls to `dsipqa/gui.py`, copying what the GUI's JavaScript or forms send (`/opt/dsiprouter/gui/static/js`, `templates`).
4. Run it once by hand against QA before relying on it in Jenkins.
