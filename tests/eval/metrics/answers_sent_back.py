"""Self-correction: answers the finish tool's checks sent back before one was accepted. Reported, not gated."""


def evaluate(instance):
    runs = instance.get("runs") or []
    return {"score": float(sum(r.get("answers_sent_back", 0) for r in runs)), "explanation": "answers sent back by the code checks"}
