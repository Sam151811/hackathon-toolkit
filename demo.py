"""D1 agent wallet: end-to-end demo against Canton DevNet.

Sets up a fresh mandate, then runs five cases in order and prints a table.
Four of the five must fail ON THE LEDGER, not in this script.

Run:  source ~/devnet.env && python3 demo.py
"""
import datetime
import json
import os
import sys

import c8lab
import agent_wallet
import time

_submit = c8lab.submit

def submit_retry(*a, **kw):
    for i in range(4):
        try:
            return _submit(*a, **kw)
        except c8lab.LabError as e:
            if "503" not in str(e) or i == 3:
                raise
            print(f"        (DevNet busy, retry {i+1})")
            time.sleep(5)

c8lab.submit = submit_retry

PKG = "#samrath-agent-wallet:Mandate"
PROPOSAL = PKG + ":MandateProposal"
MANDATE = PKG + ":Mandate"
CHARGE_RECORD = "Mandate:ChargeRecord"

OWNER = os.environ["MYPARTY"]
AGENT = os.environ["AGENT"]
SHOP = os.environ["SHOP"]
STRANGER = os.environ["STRANGER"]

CAP = "3.0"
results = []


def record(name, expected, ok, detail=""):
    results.append((name, expected, "PASS" if ok else "FAIL", detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if detail:
        print(f"        {detail}")


def reason(exc):
    """Pull the ledger's own message out of the error, not ours."""
    s = str(exc)
    for marker in ("AssertionFailed (error category 9): ",
                   '"code":"'):
        i = s.find(marker)
        if i > 0:
            tail = s[i + len(marker):]
            return tail.split('"')[0].split("\\n")[0][:80]
    return s[:80]


def setup():
    print("\nSetting up a fresh mandate")
    exp = (datetime.datetime.now(datetime.timezone.utc)
           + datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = c8lab.submit([{"CreateCommand": {
        "templateId": PROPOSAL,
        "createArguments": {
            "owner": OWNER, "spender": AGENT, "cap": CAP,
            "allowed": [SHOP], "expiresAt": exp}}}],
        act_as=OWNER, want_transaction=True)
    prop = r["transaction"]["events"][0]["CreatedEvent"]["contractId"]
    print(f"  proposal created, cap {CAP}, allow-list [shop]")

    r = c8lab.submit([{"ExerciseCommand": {
        "templateId": PROPOSAL, "contractId": prop,
        "choice": "Accept", "choiceArgument": {}}}],
        act_as=AGENT, want_transaction=True)
    for ev in r["transaction"]["events"]:
        c = ev.get("CreatedEvent")
        if c and c["templateId"].endswith("Mandate:Mandate"):
            print("  agent accepted the mandate\n")
            return c["contractId"]
    raise SystemExit("could not find the Mandate in the accept transaction")


def charge(cid, payee, amount):
    """Returns (new_cid, transferKind) or raises."""
    r = agent_wallet.charge_and_pay(cid, AGENT, OWNER, payee, amount)
    for ev in r["result"]["transaction"]["events"]:
        c = ev.get("CreatedEvent")
        if c and c["templateId"].endswith("Mandate:Mandate"):
            return c["contractId"], r["transferKind"]
    return cid, r["transferKind"]


def main():
    cid = setup()

    print("Case 1: charge 1.0 to the shop, within the cap")
    try:
        cid, kind = charge(cid, SHOP, "1.0")
        record("settles under the cap", "succeed", True,
               f"one transaction, transferKind={kind}")
    except Exception as e:
        record("settles under the cap", "succeed", False, reason(e))
        raise SystemExit("first charge failed, stopping")

    print("\nCase 2: charge 5.0, over the 3.0 cap")
    try:
        charge(cid, SHOP, "5.0")
        record("over-cap charge", "fail on ledger", False, "IT SUCCEEDED")
    except Exception as e:
        r = reason(e)
        record("over-cap charge", "fail on ledger",
               "exceed the cap" in r, r)

    print("\nCase 3: charge 0.5 to a party not on the allow-list")
    try:
        charge(cid, STRANGER, "0.5")
        record("off-allow-list charge", "fail on ledger", False, "IT SUCCEEDED")
    except Exception as e:
        r = reason(e)
        record("off-allow-list charge", "fail on ledger",
               "allow-list" in r, r)

    print("\nCase 4: owner revokes, agent cannot block it")
    c8lab.submit([{"ExerciseCommand": {
        "templateId": MANDATE, "contractId": cid,
        "choice": "Revoke", "choiceArgument": {}}}],
        act_as=OWNER, want_transaction=True)
    record("owner revokes unilaterally", "succeed", True,
           "no agent signature involved")

    print("\nCase 5: charge 0.5 after revocation")
    try:
        charge(cid, SHOP, "0.5")
        record("charge after revoke", "fail on ledger", False, "IT SUCCEEDED")
    except Exception as e:
        r = reason(e)
        record("charge after revoke", "fail on ledger",
               "CONTRACT_NOT_FOUND" in r, r)

    print("\nCase 6: the statement survives revocation")
    body = {"filter": {"filtersByParty": {OWNER: {"cumulative": [
                {"identifierFilter": {"WildcardFilter": {"value": {
                    "includeCreatedEventBlob": False}}}}]}}},
            "verbose": False, "activeAtOffset": c8lab.ledger_end()}
    records = []
    for item in c8lab.call("/v2/state/active-contracts", body):
        ev = (item.get("contractEntry", {}).get("JsActiveContract", {})
              .get("createdEvent", {}))
        if CHARGE_RECORD in ev.get("templateId", ""):
            records.append(ev["createArgument"])
    record("audit trail readable after revoke", "succeed", len(records) >= 1,
           f"{len(records)} ChargeRecord contract(s) still active")

    if records:
        print("\n  Statement:")
        for a in records:
            print(f"    {a.get('chargedAt','')[:19]}  "
                  f"{a.get('amount',''):>12}  to {a.get('payee','').split('::')[0]:<16}"
                  f"  remaining {a.get('remaining','')}")

    passed = sum(1 for r in results if r[2] == "PASS")
    print(f"\n{'='*64}")
    print(f"{passed}/{len(results)} cases behaved as specified")
    print(f"{'='*64}")
    for name, expected, status, _ in results:
        print(f"  {status:<5} {name:<38} (expected to {expected})")
    print()
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
