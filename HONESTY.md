# What is real, what is not

D1: a spend-limited wallet for an AI agent. Built solo, on DevNet, with real
Canton Coin.

Reproduce with `source ~/devnet.env && python3 demo.py`. Last clean run: 6/6
cases behaved as specified. `daml test` runs the same logic offline in about a
second.

## What is enforced on the ledger

The mandate is a Daml contract. The agent holds a choice on it, not a key to
the money. Four conditions are checked inside `Charge`, in
`daml-starter/daml/Mandate.daml`:

```daml
assertMsg "mandate expired"             (now < expiresAt)
assertMsg "amount must be positive"     (amount > 0.0)
assertMsg "payee not on the allow-list" (payee `elem` allowed)
assertMsg "charge would exceed the cap" (spent + amount <= cap)
```

Plus `ensure spent <= cap` on the template, which the ledger refuses to violate
on any code path.

Three things worth being precise about:

**The cap is cumulative, not per-charge.** `spent + amount <= cap`. The obvious
attack is ten charges each under the cap.

**Revocation cannot be blocked.** `Revoke` is consuming and controlled by
`owner` alone. The agent has no choice that delays or vetoes it. Afterwards the
agent's next attempt returns `CONTRACT_NOT_FOUND`: the authority is gone, not
merely refused.

**The payment is composed with the check.** `charge_and_pay` in
`agent_wallet.py` submits two exercises in one command list: `Charge` on the
mandate, and `TransferFactory_Transfer` on the token factory with the
registry's disclosed contracts attached. Canton commits both or neither, so
there is no ordering in which money moves and the cap check does not run.

## What I attacked myself, and what happened

All against DevNet, not a mock. Messages below are the ledger's, copied from
the actual responses.

| Attack | Result |
| --- | --- |
| Charge 5.0 against a 3.0 cap | `DAML_FAILURE ... charge would exceed the cap` |
| Pay a party not on the allow-list | `DAML_FAILURE ... payee not on the allow-list` |
| Charge after revocation | `CONTRACT_NOT_FOUND` |
| Owner charging via the agent's choice | fails, wrong controller (Daml Script) |

The first two are rules refusing an action. The third is the authority itself
no longer existing.

Expiry is tested in Daml Script with `passTime`, not on DevNet, because that
would have meant waiting out a real deadline.

## The agent is a real language model

`mcp_wallet.py` is an MCP server exposing four tools: `check_budget`, `pay`,
`statement`, `create_mandate`. Stdlib only, no dependencies.

`pay` checks nothing. It passes whatever the model asks for straight to the
ledger and reports what came back. That is deliberate: if the tool validated
first, the cap would live in my Python again, and anyone reaching the ledger
directly would walk around it. What the model sees when it overreaches:

```
-> pay({'payee': 'shop', 'amount': '99.0'})
<- REFUSED BY THE LEDGER: charge would exceed the cap. The payment did not
   happen. This was not my decision and I cannot override it.
```

## Ledger and application state drifted, in this build, today

During an MCP run a payment returned HTTP 503 from `submit-and-wait`, and my
server reported failure. The mandate's `spent` had gone from 1.0 to 2.0. The
transaction had committed; only the response was lost.

This matters more than the demo. A 503 from a submit endpoint does not mean the
command was rejected, and treating it as failure is wrong in the dangerous
direction: a naive retry double-spends against the cap. The correct handling is
to look up the command id and find out what actually happened before deciding.
My server does not do that yet. It is the same class of problem as challenge
A2, and I hit it by accident in a build small enough to notice.

## What is incomplete

**Nothing is mocked.** Several things are narrower than they look.

**All four parties are on one participant.** Owner, agent, shop and stranger
share a namespace. A real deployment would separate owner and agent across
participants, and I have not shown the authorisation model behaves identically
across a synchronizer.

**Transfers came back as `offer`, not `direct`.** The shop's
`TransferPreapproval` was proposed but the validator's automation had not
accepted it, so coin sits in a `TransferInstruction` until the shop accepts.
Mandate enforcement is unaffected, but "the money moved" is more accurately
"the money was authorised and committed to an instruction the receiver must
accept".

**Nothing enforces one mandate per agent.** I ran with two live mandates for
the same agent without noticing, and reads picked whichever came back first,
which made a successful payment look like it had done nothing. The contract
model permits several; my application assumed one. A production version would
select by explicit id, or the template would carry a contract key on (owner,
spender). The enforcement was never wrong here, my reading of it was.

**The statement is not scoped to a single mandate.** It returns every
`ChargeRecord` the owner can see.

**No per-period limits.** Lifetime cap only. The brief warned per-period turns
into date arithmetic; I made the total cap correct rather than the per-period
cap approximate.

**Canton is not trustless.** Amulet's issuer is the DSO, a named set of legal
entities, and I am trusting Cantor8's validator to host my parties honestly.
The mandate protects the owner from the agent. It does not protect either from
the participant operator.

## Bugs found in the toolkit

Three `c8lab.py` helpers break on DevNet for one shared reason: `/v2/parties`
returns at most 10,000 entries, and `find_party`, `dso_party` and
`allocate_party`'s reuse check all assume a single page. The node has ~110,000
parties, so my own party sat on page 12 and none of them could see it.

Separately, `allocate_party` grants act-as to `ledger-api-user`, which does not
exist on DevNet. The correct user is `validator-backend@clients`, the `sub`
claim of the Keycloak token. Allocation succeeds and the grant then 404s, so it
looks like allocation failed when it did not.

Workarounds: set `C8_ADMIN_PARTY` and `C8_USER` explicitly, hold party ids in
environment variables, and page `/v2/parties` when a lookup is unavoidable.
