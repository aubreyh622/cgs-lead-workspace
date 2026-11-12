"""Cost: thousands of prompt tokens over the case's agent runs (cache reads included). Reported, not gated."""


def evaluate(instance):
    runs = instance.get("runs") or []
    return {"score": sum(r.get("tokens_in", 0) for r in runs) / 1000, "explanation": "thousands of prompt tokens"}
