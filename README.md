# D1: a spend-limited wallet for an AI agent

Solo entry, Cantor8 Canton hackathon. Built on the Daml track, running on
DevNet with real Canton Coin.

The premise from the brief: giving an agent a hot key is indefensible, because
if the agent goes wrong there is nothing between it and your money. This is the
wallet an agent should have instead. The agent holds a *choice on a contract*,
not a key. The cap, the allow-list and the expiry are enforced in Daml, so
there is no API to go around.

## Run it

```bash
source ~/devnet.env
python3 demo.py
```

Sets up a fresh mandate, runs six cases, prints a table. Four of the six must
fail on the ledger. Last clean run: 6/6.

Offline, no network needed:

```bash
cd daml-starter && daml build && daml test
```

## The line that does the work

`daml-starter/daml/Mandate.daml`, inside the `Charge` choice:

```daml
assertMsg "mandate expired"             (now < expiresAt)
assertMsg "amount must be positive"     (amount > 0.0)
assertMsg "payee not on the allow-list" (payee `elem` allowed)
assertMsg "charge would exceed the cap" (spent + amount <= cap)
```

The cap is cumulative (`spent + amount`), not per-charge. `ensure spent <= cap`
on the template means the ledger will not create a mandate that violates it on
any path.

`Revoke` is consuming and controlled by `owner` alone, so the agent cannot
block or delay it. Afterwards the agent gets `CONTRACT_NOT_FOUND`: the
authority is gone, not merely refused.

## What I attacked, and what the ledger said

| Attack | Result |
| --- | --- |
| Charge 5.0 against a 3.0 cap | `DAML_FAILURE ... charge would exceed the cap` |
| Pay a party not on the allow-list | `DAML_FAILURE ... payee not on the allow-list` |
| Charge after revocation | `CONTRACT_NOT_FOUND` |
| Owner using the agent's choice | fails, wrong controller |
| Charge after expiry | fails (Daml Script, `passTime`) |
| Audit trail after revocation | survives, still queryable |

## Composed settlement

`agent_wallet.py` submits two exercises in one command list: `Charge` on the
mandate, and `TransferFactory_Transfer` on the token factory with the
registry's disclosed contracts attached. Canton commits both or neither, so
there is no ordering in which the money moves and the cap check does not run.

## The agent is a language model

`mcp_wallet.py` is an MCP server, stdlib only, no dependencies. Four tools:
`check_budget`, `pay`, `statement`, `create_mandate`.

`pay` validates nothing. It passes whatever the model asks for to the ledger
and reports the answer, deliberately: a cap enforced in the tool is a cap
anyone reaching the ledger directly can skip.

```
-> pay({'payee': 'shop', 'amount': '99.0'})
<- REFUSED BY THE LEDGER: charge would exceed the cap. The payment did not
   happen. This was not my decision and I cannot override it.
```

Wire it into an MCP client with:

```json
{ "command": "python3", "args": ["/home/<you>/hackathon-toolkit/mcp_wallet.py"] }
```

## Files

| File | What |
| --- | --- |
| `daml-starter/daml/Mandate.daml` | The mandate, the charge record, the rules |
| `daml-starter/daml/Test.daml` | Four Daml Script tests, run with `daml test` |
| `agent_wallet.py` | Composed settlement: cap check + transfer, one transaction |
| `demo.py` | Six cases end to end against DevNet |
| `mcp_wallet.py` | MCP server so a language model can hold the wallet |
| `HONESTY.md` | What is enforced, what is narrow, what broke |

## Read HONESTY.md

It covers what is not finished, a ledger/application drift I hit during
development and have not fixed, and two toolkit bugs that cost me an afternoon.
Shortest useful summary of where this actually stands.
