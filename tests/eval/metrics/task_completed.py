"""1 when every agent run of the case ended with an accepted answer (finish tool), else 0."""


def evaluate(instance):
    runs = instance.get("runs") or []
    done = bool(runs) and all(r.get("finished") and r.get("status") == "completed" for r in runs)
    why = "; ".join(f"{r['agent']}: {r['status']}, {'answered' if r.get('finished') else 'no accepted answer'}"
                    + (f" ({r['error'][:120]})" if r.get("error") else "") for r in runs) or "no agent run was recorded"
    return {"score": 1.0 if done else 0.0, "explanation": why}
