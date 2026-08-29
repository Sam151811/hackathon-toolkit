"""MCP server: gives a language model a spend-limited wallet.

The model gets four tools. It can call `pay` with any payee and any amount it
likes. Nothing here checks the cap or the allow-list, deliberately: the rules
live in Daml, and the ledger refuses. This file only reports what happened.

Stdlib only, no pip install, same reasoning as c8lab.py.

Run:  source ~/devnet.env && python3 mcp_wallet.py
Wire into an MCP client with:
  { "command": "python3", "args": ["/home/<you>/hackathon-toolkit/mcp_wallet.py"] }
"""
import datetime
import json
import os
import sys
import traceback

import c8lab
import agent_wallet

PKG = "#samrath-agent-wallet:Mandate"
PROPOSAL = PKG + ":MandateProposal"
MANDATE_T = PKG + ":Mandate"

OWNER = os.environ["MYPARTY"]
AGENT = os.environ["AGENT"]

# Friendly names, so the model never handles a 70-character party id.
PAYEES = {"shop": os.environ["SHOP"], "stranger": os.environ["STRANGER"]}

STATE = {"mandate": ""}


def log(msg):
    """MCP talks JSON-RPC on stdout, so anything human goes to stderr."""
    print(msg, file=sys.stderr, flush=True)


def _new_mandate_cid(tx):
    for ev in tx.get("transaction", {}).get("events", []):
        c = ev.get("CreatedEvent")
        if c and c["templateId"].endswith("Mandate:Mandate"):
            return c["contractId"]
    return None


def _read_mandate():
    body = {"filter": {"filtersByParty": {OWNER: {"cumulative": [
                {"identifierFilter": {"WildcardFilter": {"value": {
                    "includeCreatedEventBlob": False}}}}]}}},
            "verbose": False, "activeAtOffset": c8lab.ledger_end()}
    out = []
    for item in c8lab.call("/v2/state/active-contracts", body):
        ev = (item.get("contractEntry", {}).get("JsActiveContract", {})
              .get("createdEvent", {}))
        tid = ev.get("templateId", "")
        if tid.endswith("Mandate:Mandate") or "Mandate:ChargeRecord" in tid:
            out.append((tid, ev["contractId"], ev["createArgument"]))
    return out


# ---------------------------------------------------------------- tools

def tool_create_mandate(cap="3.0", allowed=None, hours=24):
    """Owner-side setup. Not something the agent would normally do."""
    allowed = allowed or ["shop"]
    parties = [PAYEES[a] for a in allowed]
    exp = (datetime.datetime.now(datetime.timezone.utc)
           + datetime.timedelta(hours=int(hours))).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = c8lab.submit([{"CreateCommand": {
        "templateId": PROPOSAL,
        "createArguments": {"owner": OWNER, "spender": AGENT, "cap": str(cap),
                            "allowed": parties, "expiresAt": exp}}}],
        act_as=OWNER, want_transaction=True)
    prop = r["transaction"]["events"][0]["CreatedEvent"]["contractId"]
    r = c8lab.submit([{"ExerciseCommand": {
        "templateId": PROPOSAL, "contractId": prop,
        "choice": "Accept", "choiceArgument": {}}}],
        act_as=AGENT, want_transaction=True)
    STATE["mandate"] = _new_mandate_cid(r)
    return (f"Mandate created and accepted. Cap {cap}, may pay "
            f"{', '.join(allowed)}, expires in {hours}h.")


def tool_check_budget():
    for tid, cid, arg in _read_mandate():
        if tid.endswith("Mandate:Mandate"):
            cap, spent = float(arg["cap"]), float(arg["spent"])
            names = [n for n, p in PAYEES.items() if p in arg["allowed"]]
            return (f"Cap {cap}, spent {spent}, remaining {cap - spent}. "
                    f"May pay: {', '.join(names) or '(nobody)'}. "
                    f"Expires {arg['expiresAt']}.")
    return "No active mandate. The agent currently has no spending authority."

def _live_mandate():
    """Find the current mandate rather than trusting a stale env var.
    Charge archives and recreates it, so the id changes on every payment."""
    for tid, cid, _ in _read_mandate():
        if tid.endswith("Mandate:Mandate"):
            return cid
    return None

def tool_pay(payee, amount):
    """The model calls this. Nothing below checks anything."""
    if payee not in PAYEES:
        return (f"Unknown payee '{payee}'. Known names: "
                f"{', '.join(PAYEES)}. (This is a lookup failure in the "
                f"tool, not a ledger decision.)")
    STATE["mandate"] = STATE["mandate"] or _live_mandate()
    if not STATE["mandate"]:
        return "No mandate on file. Ask the owner to create one."
    try:
        r = agent_wallet.charge_and_pay(
            STATE["mandate"], AGENT, OWNER, PAYEES[payee], str(amount))
    except c8lab.LabError as e:
        s = str(e)
        i = s.find("category 9): ")
        if i > 0:
            why = s[i + 13:].split('"')[0]
            return (f"REFUSED BY THE LEDGER: {why}. The payment did not "
                    f"happen. This was not my decision and I cannot "
                    f"override it.")
        if "CONTRACT_NOT_FOUND" in s:
            fresh = _live_mandate()
            if fresh and fresh != STATE["mandate"]:
                STATE["mandate"] = fresh
                return tool_pay(payee, amount)
            return ("REFUSED: the mandate no longer exists. The owner has "
                    "revoked it. I have no spending authority at all now.")
        return f"Failed: {s[:200]}"
    new = _new_mandate_cid(r["result"])
    if new:
        STATE["mandate"] = new
    return (f"Paid {amount} to {payee}. transferKind="
            f"{r['transferKind']}. The cap check and the transfer were in "
            f"one transaction.")


def tool_statement():
    rows = [arg for tid, _, arg in _read_mandate()
            if "ChargeRecord" in tid]
    if not rows:
        return "No charges recorded."
    rows.sort(key=lambda a: a.get("chargedAt", ""))
    lines = ["date                 amount        payee       remaining"]
    for a in rows:
        name = next((n for n, p in PAYEES.items() if p == a["payee"]),
                    a["payee"].split("::")[0])
        lines.append(f"{a['chargedAt'][:19]}  {float(a['amount']):>8.2f}  "
                     f"{name:<12} {float(a['remaining']):>8.2f}")
    return "\n".join(lines)


TOOLS = [
    {"name": "check_budget",
     "description": "What may I spend, on what, and until when. Call this "
                    "before paying if you are unsure.",
     "inputSchema": {"type": "object", "properties": {}},
     "fn": lambda **k: tool_check_budget()},
    {"name": "pay",
     "description": "Pay someone from the owner's wallet. The spending rules "
                    "are enforced on the ledger, not here, so this may be "
                    "refused.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "payee": {"type": "string",
                                   "description": "shop or stranger"},
                         "amount": {"type": "string",
                                    "description": "e.g. 1.0"}},
                     "required": ["payee", "amount"]},
     "fn": lambda **k: tool_pay(k["payee"], k["amount"])},
    {"name": "statement",
     "description": "Every charge made under this mandate, with what was "
                    "left after each.",
     "inputSchema": {"type": "object", "properties": {}},
     "fn": lambda **k: tool_statement()},
    {"name": "create_mandate",
     "description": "Owner-only setup: issue a fresh mandate to the agent.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "cap": {"type": "string"},
                         "allowed": {"type": "array",
                                     "items": {"type": "string"}},
                         "hours": {"type": "integer"}}},
     "fn": lambda **k: tool_create_mandate(**k)},
]


# ------------------------------------------------------------- protocol

def handle(req):
    m = req.get("method")
    if m == "initialize":
        return {"protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agent-wallet", "version": "0.1.0"}}
    if m == "tools/list":
        return {"tools": [{k: t[k] for k in
                           ("name", "description", "inputSchema")}
                          for t in TOOLS]}
    if m == "tools/call":
        p = req.get("params", {})
        name, args = p.get("name"), p.get("arguments", {}) or {}
        for t in TOOLS:
            if t["name"] == name:
                log(f"-> {name}({args})")
                try:
                    text = t["fn"](**args)
                except Exception as e:
                    text = f"Error: {e}"
                    log(traceback.format_exc())
                log(f"<- {text.splitlines()[0][:100]}")
                return {"content": [{"type": "text", "text": text}]}
        return {"content": [{"type": "text", "text": f"No tool '{name}'"}],
                "isError": True}
    return None


def main():
    log("agent-wallet MCP server ready")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in req:          # a notification, no reply expected
            continue
        try:
            result = handle(req)
        except Exception as e:
            log(traceback.format_exc())
            print(json.dumps({"jsonrpc": "2.0", "id": req["id"],
                              "error": {"code": -32603,
                                        "message": str(e)}}), flush=True)
            continue
        if result is None:
            print(json.dumps({"jsonrpc": "2.0", "id": req["id"],
                              "error": {"code": -32601,
                                        "message": "method not found"}}),
                  flush=True)
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": req["id"],
                              "result": result}), flush=True)


if __name__ == "__main__":
    main()
