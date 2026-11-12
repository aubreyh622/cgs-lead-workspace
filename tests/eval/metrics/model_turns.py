"""Effort: model turns (AI requests) the case took, over all its agent runs. Reported, not gated."""


def evaluate(instance):
    runs = instance.get("runs") or []
    return {"score": float(sum(r.get("steps", 0) for r in runs)), "explanation": ", ".join(f"{r['agent']} {r.get('steps', 0)}" for r in runs)}
