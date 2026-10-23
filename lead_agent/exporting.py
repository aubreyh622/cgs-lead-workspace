import csv
import io
import json


def inspection_csv(leads):
    out = io.StringIO(newline="")
    fields = ["schema_version", "run_id", "lead_id", "mode", "company", "domain", "status",
              "identification_priority", "coverage", "rubric_version", "record_json"]
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for lead in leads:
        row = {k: getattr(lead, k) for k in ("schema_version", "run_id", "lead_id", "mode")}
        row.update(company=lead.research.company, domain=lead.research.domain, status=lead.review.decision,
                   identification_priority=lead.priority.score, coverage=lead.priority.coverage,
                   rubric_version=lead.priority.rubric_version, record_json=lead.model_dump_json())
        # Spreadsheet formula protection for user/model supplied cells.
        writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v
                         for k, v in row.items()})
    return out.getvalue()


def evaluation(store, run_id, labels=None, total_cost_usd=None):
    if labels is not None and (not isinstance(labels, list) or any(
            not isinstance(x, dict) or not isinstance(x.get("domain"), str) or type(x.get("relevant")) is not bool
            for x in labels)):
        raise ValueError("Labels require a domain and a boolean relevant field.")
    run = store.run(run_id)["payload"]
    leads = store.leads(run_id)
    ranked = sorted((l for l in leads if l.priority.score is not None),
                    key=lambda l: (-l.priority.score, l.lead_id))
    reviewed = [l for l in leads if l.review.reviewer]
    approved = [l for l in leads if l.review.decision == "Approved for handoff"]
    top = ranked[:10]
    top_reviewed = [l for l in top if l.review.reviewer]
    supported = [l for l in reviewed if l.review.source_support_checked]
    with_people = [l for l in reviewed if l.research.people]
    role_checked = [l for l in with_people if l.review.role_accuracy_checked is not None]
    from .models import domain
    label_map = {domain(x["domain"]): x for x in labels or []}
    # Labels must be held out and supplied by CGS; fixture labels are not business validation.
    labelled = [(l, label_map[l.research.domain]) for l in top if l.research.domain in label_map]
    thresholds = run.get("evaluation_thresholds", {})
    return {
        "run_id": run_id, "strategy": run["strategy"], "mode": run["mode"],
        "brief_version": run["brief"]["version"], "rubric_version": run["rubric"]["version"],
        "target": run["target"], "identified": len(leads), "ranked": len(ranked),
        "reviewed": len(reviewed), "approved": len(approved), "top_n": len(top),
        "top_n_reviewed": len(top_reviewed),
        "approval_in_top_n": sum(l.review.decision == "Approved for handoff" for l in top_reviewed),
        "approval_precision_at_10": (sum(l.review.decision == "Approved for handoff" for l in top) / 10
                                     if len(top) == 10 and len(top_reviewed) == 10 else None),
        "source_support_reviewed": len(supported), "reviewed_with_people": len(with_people),
        "role_accuracy_reviewed": len(role_checked),
        "role_accuracy_rate": (sum(l.review.role_accuracy_checked is True for l in role_checked) / len(role_checked)
                               if role_checked else None),
        "duplicate_records": len(leads) - len({l.research.domain for l in leads}),
        "needs_research": sum(bool(l.validation_issues) for l in leads),
        "reviewer_minutes": sum(l.review.minutes for l in reviewed),
        "total_cost_usd": total_cost_usd,
        "cost_per_approved_usd": total_cost_usd / len(approved) if total_cost_usd is not None and approved else None,
        "active_seconds": run["active_seconds"], "attempts": run["counters"],
        "reserved_tavily_credits": run["reserved_credits"], "provider_reported_credits": run["reported_credits"],
        "model_usage": run["usage"], "held_out_top_labels": len(labelled),
        "held_out_relevance_rate": sum(x["relevant"] for _, x in labelled) / len(labelled) if labelled else None,
        "acceptance_thresholds": thresholds,
        "acceptance_status": "Pending CGS thresholds and complete held-out review",
        "note": "Unknown costs, absent labels and incomplete reviews are null, not zero. Freeze configs before comparison."
    }
