"""Composed settlement: the cap check and the payment in one atomic submission.

If Charge fails, the transfer never commits. There is no API path around the
cap because both exercises are in the same transaction.
"""
import datetime
import c8lab

MANDATE = "#daml-starter:Mandate:Mandate"


def charge_and_pay(mandate_cid, agent, sender, receiver, amount,
                   instrument="Amulet", hours=24):
    admin = c8lab.admin_party()
    hs = c8lab.holdings(sender)
    spendable = [h for h in hs if not h["locked"]
                 and h["instrument"] == instrument and h["admin"] == admin]
    if not spendable:
        raise c8lab.LabError(f"no spendable {instrument} for {sender}")

    t0 = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    args = {"expectedAdmin": admin,
            "transfer": {"sender": sender, "receiver": receiver,
                         "amount": str(amount),
                         "instrumentId": {"admin": admin, "id": instrument},
                         "requestedAt": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "executeBefore": (t0 + datetime.timedelta(hours=hours)
                                           ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "inputHoldingCids": [h["contractId"] for h in spendable],
                         "meta": {"values": {}}},
            "extraArgs": {"context": {"values": {}}, "meta": {"values": {}}}}

    fac = c8lab.registry("/registry/transfer-instruction/v1/transfer-factory",
                         {"choiceArguments": args})
    cc = fac.get("choiceContext", {})
    args["extraArgs"]["context"] = cc.get("choiceContextData", {})

    cmds = [
        {"ExerciseCommand": {"templateId": MANDATE, "contractId": mandate_cid,
                             "choice": "Charge",
                             "choiceArgument": {"amount": str(amount),
                                                "payee": receiver}}},
        {"ExerciseCommand": {"templateId": c8lab.TRANSFER_FACTORY,
                             "contractId": fac["factoryId"],
                             "choice": "TransferFactory_Transfer",
                             "choiceArgument": args}},
    ]
    res = c8lab.submit(cmds, act_as=[agent, sender],
                       disclosed=cc.get("disclosedContracts", []),
                       want_transaction=True)
    return {"transferKind": fac.get("transferKind"), "result": res}