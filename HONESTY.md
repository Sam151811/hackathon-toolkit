# What is real, what is not

D1: a spend-limited wallet for an AI agent. Built solo, on DevNet, with real
Canton Coin.

Run `python3 demo.py` with `~/devnet.env` sourced to reproduce everything below.
Last run: 6/6 cases behaved as specified.

## What is enforced on the ledger

The mandate is a Daml contract. The agent holds a choice on it, not a key to
the money. Four conditions are checked inside `Charge`, in
`daml-starter/daml/Mandate.daml`:

```daml
assertMsg "mandate expired"          (now < expiresAt)
assertMsg "amount must be positive"  (amount > 0.0)
assertMsg "payee not on the allow-list" (payee `elem` allowed)
assertMsg "charge would exceed the cap" (spent + amount <= cap)
```

Plus `ensure spent <= cap` on the template itself, which the ledger refuses to
violate on any code path.

Two things I want to be precise about, because they are the parts that matter:

**The cap is cumulative, not per-charge.** `spent + amount <= cap`. The obvious
attack is ten charges each under the cap.

**Revocation cannot be blocked.** `Revoke` is consuming and controlled by
`owner` alone. The agent is a signatory of the mandate, so it consented to the
arrangement, but consuming a contract needs only the controller's authority
here. The agent has no choice that delays or vetoes it. After revocation the
agent's next attempt returns `CONTRACT_NOT_FOUND`: the authority is gone, not
merely refused.

**The payment is composed with the check.** `charge_and_pay` in
`agent_wallet.py` submits two exercises in one command list: `Charge` on the
mandate, and `TransferFactory_Transfer` on the token factory with the
registry's disclosed contracts attached. Canton commits both or neither. So
there is no ordering in which the money moves and the cap check does not run.

## What I attacked myself, and what happened

All four ran against DevNet, not a mock. The messages below are the ledger's,
copied from the actual responses.

| Attack | Result |
| --- | --- |
| Charge 5.0 against a 3.0 cap | `DAML_FAILURE ... charge would exceed the cap` |
| Pay a party not on the allow-list | `DAML_FAILURE ... payee not on the allow-list` |
| Charge after revocation | `CONTRACT_NOT_FOUND` |
| Owner charging via the agent's choice | fails, wrong controller (Daml Script) |

The distinction between the first two and the third is worth noting. The first
two are rules refusing an action. The third is the authority itself no longer
existing.

Expiry is tested in Daml Script with `passTime`, not on DevNet, because I would
have had to wait out a real deadline.

## What is mocked or incomplete

**Nothing is mocked.** But several things are narrower than they look.

**All four parties are on one participant.** Owner, agent, shop and stranger
share a namespace. A real agent wallet would put the owner and the agent on
different participants, and I have not tested that the authorisation model
behaves identically across a synchronizer. I believe it does, but I have not
shown it.

**Transfers came back as `offer`, not `direct`.** The shop's
`TransferPreapproval` proposal was created but the validator's automation had
not accepted it by demo time, so the coin sits in a `TransferInstruction` until
the shop accepts. The mandate enforcement is unaffected either way, but "the
money moved" is more accurately "the money was authorised and committed to an
instruction the receiver must accept".

**The statement query is not scoped to one mandate.** It returns every
`ChargeRecord` the owner can see. Correct for an audit trail, but the count in
the demo output includes charges from earlier runs.

**No per-period limits.** Total lifetime cap only. The brief warned that
per-period turns into date arithmetic, and I chose to make the total cap
correct rather than the per-period cap approximate.

**No frontend and no MCP server.** The agent in this demo is a Python function,
not a language model. Nothing in the design depends on which of the two it is,
since the enforcement is on the ledger, but I have not demonstrated it with a
real model.

**One participant's word.** Canton is not trustless. The Amulet issuer is the
DSO, a named set of legal entities, and I am trusting Cantor8's validator to
host my parties honestly. The mandate protects the owner from the agent. It
does not protect either from the participant operator.

## Bugs found in the toolkit

Three `c8lab.py` helpers break on DevNet for one shared reason: `/v2/parties`
returns at most 10,000 entries, and `find_party`, `dso_party` and
`allocate_party`'s reuse check all assume a single page. The node has ~110,000
parties, so my own party sat on page 12 and none of them could see it.

Separately, `allocate_party` grants act-as to `ledger-api-user`, which does not
exist on DevNet. The correct user is `validator-backend@clients`, the `sub`
claim of the Keycloak token. Party allocation succeeds and then the grant 404s,
so it looks like allocation failed when it did not.

Workarounds: pass `C8_ADMIN_PARTY` and `C8_USER` explicitly, hold party IDs in
environment variables, and page `/v2/parties` when a lookup is unavoidable.
