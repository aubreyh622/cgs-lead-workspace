from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from lead_agent.assessment import normalized
from lead_agent.exporting import evaluation, inspection_csv
from lead_agent.models import Brief, Lead, Limits, Review, Rubric, Seed
from lead_agent.providers import LiveProviders, ProviderError
from lead_agent.storage import Store
from lead_agent.worker import Worker, new_payload

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
st.set_page_config(page_title="CGS Company Research", page_icon="🔎", layout="wide")
store = Store(ROOT / "data" / "identification.sqlite3")
brief = Brief.model_validate_json((ROOT / "config/problem_brief.json").read_text(encoding="utf-8"))
rubric = Rubric.model_validate_json((ROOT / "config/rubric.json").read_text(encoding="utf-8"))


def providers():
    return LiveProviders(st.session_state.get("gemini_key", ""),
                         st.session_state.get("tavily_key", ""), st.session_state.get("model_id", ""))


with st.sidebar:
    st.title("CGS Research")
    st.caption("Company evidence → review → handoff")
    with st.expander("API connections", expanded=True):
        st.text_input("Gemini API key", type="password", key="gemini_key", value=os.getenv("GEMINI_API_KEY", ""))
        st.text_input("Tavily API key", type="password", key="tavily_key", value=os.getenv("TAVILY_API_KEY", ""))
        st.text_input("Gemini model ID", key="model_id", value=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
                      placeholder="Model available in your account")
        st.caption("Enter keys here, not in chat. They stay in this session. An optional local .env is also supported.")
        st.markdown("[Get a Gemini key](https://aistudio.google.com/apikey) · [Tavily account](https://app.tavily.com/)")
        if st.button("Test connections", width="stretch"):
            try:
                with st.spinner("Testing model, search and page retrieval…"):
                    diagnostics = providers().test_connections()
                for result in diagnostics:
                    message = f"{result['service']}: {result['message']}"
                    if result["ok"]:
                        st.success(message)
                    else:
                        st.error(message)
            except ProviderError as exc:
                st.error(str(exc))
        st.caption("Test: one small model request, one search and one page retrieval. Provider usage may apply.")
    with st.expander("Advanced"):
        show_examples = st.checkbox("Show historical and synthetic runs", value=False)
        model_rpm = st.number_input("Model requests per minute", min_value=1, max_value=30, value=4)
        st.caption("Four requests/minute leaves room under the five/minute free-tier limit observed in the live test. Shared account usage can still cause a pause.")
        st.caption("Scoring is provisional. Edit config/rubric.json and config/problem_brief.json to change definitions for new runs.")
        st.download_button("Download current rubric", rubric.model_dump_json(indent=2), "rubric.json", "application/json")
        st.download_button("Download lead schema", json.dumps(Lead.model_json_schema(), indent=2), "lead.schema.json", "application/json")

st.title("Find a reason to investigate")
st.write("Research a company, see what changed and assess where CGS could help.")
st.caption("Focus: AI and digital transformation strategy · Manufacturers with US operations · Provisional scoring")

with st.expander("Start new research", expanded=not bool(st.session_state.get("run_id"))):
    path = st.radio("Research scope", ["One company", "Five-company batch"], horizontal=True)
    with st.form("research"):
        if path == "One company":
            left, right = st.columns(2)
            company = left.text_input("Company name", "Ford")
            company_domain = right.text_input("Company website", "ford.com")
            entries = [{"company": company, "domain": company_domain}]
        else:
            st.write("These five known companies are research inputs, not approved prospects.")
            with (ROOT / "examples/seed_companies.csv").open(encoding="utf-8-sig", newline="") as seed_file:
                defaults = list(csv.DictReader(seed_file))
            entries = st.data_editor(defaults, hide_index=True, width="stretch", num_rows="fixed")
        st.caption("Checks identity, researches business events, gathers source excerpts and looks for likely buyers. Missing information stays visible.")
        count = 1 if path == "One company" else 5
        st.caption(f"Run cap: {8 * count} searches · {10 * count} page retrievals · {6 * count} model requests · {5 * count} active minutes. Start with one company to check quality and usage.")
        start = st.form_submit_button("Research company" if count == 1 else "Research five companies", type="primary")
    if start:
        try:
            seeds = [Seed.model_validate(x).model_dump() for x in entries]
            if len({s["domain"] for s in seeds}) != len(seeds):
                raise ValueError("Each company must have a different website domain.")
            live = providers()
            live.check()
            limits = Limits(searches=8 * count, extractions=10 * count, model_calls=6 * count,
                            tavily_credits=18 * count, active_seconds=300 * count, model_requests_per_minute=model_rpm)
            payload = new_payload(brief, rubric, seeds, strategy="seeds", limits=limits, model=live.model,
                                  prefetch_sources=True)
            run_id = store.create_run(payload)
            token = store.claim(run_id)
            threading.Thread(target=Worker(store, live).execute, args=(run_id, token), daemon=True).start()
            st.session_state.run_id = run_id
            st.rerun()
        except (ValueError, ProviderError) as exc:
            st.error(str(exc))

available = []
for row in store.runs():
    payload = store.run(row["id"])["payload"]
    if show_examples or (payload["mode"] == "live" and payload.get("research_method", "agent") == "agent"):
        names = ", ".join(s["company"] for s in payload["seeds"])
        method = " · sample" if payload["mode"] == "synthetic" or payload.get("research_method") == "manual_public_web" else ""
        label = f"{names or 'Market discovery'} · {payload['created_at'][:16].replace('T', ' ')} UTC · {row['status']}{method}"
        available.append((row["id"], label))

if not available:
    st.info("Ready for the first real research run. Enter your API connections, test them, then research one company.")
    st.stop()

choices = [r[0] for r in available]
preferred = st.session_state.get("run_id")
selected = st.selectbox("Research history", choices, index=choices.index(preferred) if preferred in choices else 0,
                        format_func=dict(available).get)
st.session_state.run_id = selected
running = store.run(selected)["status"] == "running"


@st.fragment(run_every="3s" if running else None)
def results_panel(run_id):
    row = store.run(run_id)
    state = row["payload"]
    if running and row["status"] != "running":
        st.rerun()
    leads = store.leads(run_id)
    active = row["status"] == "running"
    if state["mode"] == "synthetic":
        st.warning("Historical fictional test data. This run tests software behavior, not real lead discovery.")
    elif state.get("research_method") == "manual_public_web":
        st.warning("Historical manual excerpt samples. These are not results from the live agent.")
    else:
        st.subheader("Research results")
    complete = state.get("seed_index", 0) if state["strategy"] == "seeds" else len(leads)
    if active:
        current = state["seeds"][complete]["company"] if complete < len(state["seeds"]) else "Finalizing"
        st.info(f"Researching {current} · {complete} of {state['target']} companies finished. Updates automatically.")
        st.progress(min(complete / state["target"], 1.0))
    elif row["status"] == "completed":
        st.success(f"Research pass finished · {len(leads)} company cards. Review the evidence and gaps below.")
    else:
        st.warning(f"Research {row['status']}: {row['error'] or 'Progress has been saved.'}")
    controls = st.columns([1, 1, 3])
    if active and controls[0].button("Stop research"):
        store.stop(run_id)
        st.info("Stopping after the current request.")
    if not active and row["status"] in ("pending", "paused", "failed", "running") and state["mode"] == "live":
        if controls[0].button("Resume research"):
            try:
                live = providers()
                live.check()
                if live.model != state["model"]:
                    raise ValueError("Resume with the original model ID, or start a new run.")
                token = store.claim(run_id)
                threading.Thread(target=Worker(store, live).execute, args=(run_id, token), daemon=True).start()
                st.rerun()
            except (ValueError, ProviderError) as exc:
                st.error(str(exc))
    if controls[1].button("Refresh results"):
        st.rerun()
    with st.expander("Research progress and diagnostics"):
        if state.get("source_snapshot_run_id"):
            st.caption("This model assessment reuses previously retrieved public sources. Request counts below cover new requests only; source-acquisition usage belongs to the original run.")
        st.caption(f"Requests: {state['counters']['searches']} searches · {state['counters']['extractions']} page retrievals · {state['counters']['model_calls']} model calls. Active time: {state['active_seconds'] / 60:.1f} minutes.")
        if state["limits"].get("model_requests_per_minute"):
            st.caption(f"Model pacing: {state['limits']['model_requests_per_minute']} requests/minute. Waiting between requests is normal.")
        for event in state["events"][-12:]:
            st.text(event["message"])
        if row["error"]:
            st.write(row["error"])
        st.caption(f"Brief {state['brief']['version']} · Rubric {state['rubric']['version']} · Model {state['model'] or 'none'}")
    if not leads:
        st.caption("A card appears when the agent saves its findings. Provider failures remain visible here.")
        return
    ordered = sorted(leads, key=lambda l: -(l.priority.score if l.priority.score is not None else -1))
    if len(ordered) > 1:
        st.dataframe([{"Company": l.research.company, "Priority": l.priority.band.capitalize(),
                       "Review": l.review.decision, "Buyer": "Verified" if l.research.people else "Unknown"}
                      for l in ordered], hide_index=True, width="stretch")
    chosen = st.selectbox("Company to inspect", [l.lead_id for l in ordered],
                          format_func={l.lead_id: l.research.company for l in ordered}.get, key="company_" + run_id)
    lead = next(l for l in ordered if l.lead_id == chosen)
    r = lead.research
    with st.container(border=True):
        st.subheader(r.company)
        st.caption(f"{r.domain} · {lead.review.decision}")
        priority_label = "Priority pending" if lead.priority.score is None else lead.priority.band.capitalize() + " priority"
        confidence_label = "pending evidence checks" if lead.validation_issues else r.confidence
        st.write(f"**{priority_label}** · {'Provisional' if lead.priority.provisional else 'Calibrated'} · Confidence: {confidence_label} · Buyer: {'verified' if r.people else 'unknown'}")
        source_map = {s.source_id: s for s in lead.sources}
        supported = [e for e in r.evidence if e.source_id in source_map
                     and normalized(e.excerpt) in normalized(source_map[e.source_id].content)]
        if lead.validation_issues:
            st.warning("Research incomplete. Claims and hypotheses below require correction before they can support handoff.")
        st.markdown("**What the sources say**")
        events = [e for e in supported if e.category in ("signal", "problem", "timing")]
        for e in events:
            st.write(e.excerpt if len(e.excerpt) <= 320 else e.excerpt[:320].rsplit(" ", 1)[0] + "…")
        if not events:
            st.write("No supported business event or challenge has been established yet.")
        for label, value in (("Possible challenge · hypothesis", r.problem_hypothesis),
                             ("Where CGS could help · hypothesis", r.cgs_relevance),
                             ("Another explanation", r.alternative_explanation)):
            st.markdown(f"**{label}**")
            st.write(value)
        accounts = [e.observed_fact for e in supported if e.category == "account"]
        if accounts:
            st.markdown("**Company and buyer context**")
            for fact in accounts:
                st.write(fact)
            st.caption("Company scale and seniority do not establish consulting budget or buying intent.")
        if r.people:
            st.markdown("**Likely buyers**")
            for p in r.people:
                basis = f"dated role evidence: {p.role_as_of}" if p.role_as_of else "listed on an undated official leadership page"
                st.write(f"{p.name} · {p.role} · {basis}; checked {p.verified_at}")
        st.markdown("**Next investigation**")
        st.write(r.suggested_next_investigation)
        if lead.validation_issues:
            st.warning("Evidence issues: " + " · ".join(lead.validation_issues))
        with st.expander("Missing information"):
            for gap in dict.fromkeys(lead.research_gaps + r.unanswered_questions):
                st.write("• " + gap)
        with st.expander("Sources and evidence"):
            for e in r.evidence:
                s = source_map.get(e.source_id)
                if e not in supported:
                    st.warning("Unverified proposed claim; not supported by a retrieved excerpt.")
                st.write(f"**{e.category.capitalize()} · agent interpretation to verify:** {e.observed_fact}")
                st.text(e.excerpt)
                if s:
                    st.markdown(f"[Open source]({s.url})")
                    st.caption(f"Published: {e.publication_date or 'not stated'} · Retrieved: {s.retrieved_at.date()}")
        with st.expander("Priority calculation"):
            st.caption(f"Score on assessed dimensions: {lead.priority.score if lead.priority.score is not None else 'not available'} · Weight coverage: {lead.priority.coverage:.0%} · Possible full score: {lead.priority.possible_range}")
            for name, component in lead.priority.components.items():
                st.write(f"**{name.replace('_', ' ').capitalize()}: {component.assessment or 'unknown'}** — {component.reason}")
            st.caption(f"Rubric {lead.priority.rubric_version}. Missing fields are excluded. Priority is provisional, not a probability or a deal-value rating.")
        if lead.review.reviewer:
            st.caption(f"Last review: {lead.review.reviewer} · {lead.review.decision}")
            st.write(lead.review.notes)
        with st.expander("Review this company"):
            if active:
                st.info("Review becomes available when research stops. Further research resets a decision and preserves its history.")
            with st.form("review_" + run_id + lead.lead_id):
                decisions = ["Ready for review", "Approved for handoff", "Deferred", "Rejected", "Needs research"]
                decision = st.selectbox("Decision", decisions, index=decisions.index(lead.review.decision))
                reviewer = st.text_input("Reviewer name", value=lead.review.reviewer or "")
                notes = st.text_area("Review notes", value=lead.review.notes)
                support = st.checkbox("I checked the material claims and company identity against the sources.")
                roles = st.checkbox("I checked the named buyers' roles.") if r.people else None
                if st.form_submit_button("Save review", disabled=active):
                    try:
                        store.review(run_id, lead.lead_id, Review(decision=decision, reviewer=reviewer,
                                     reviewed_at=datetime.now(timezone.utc), notes=notes, minutes=lead.review.minutes,
                                     source_support_checked=support, role_accuracy_checked=roles))
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
    with st.expander("Export reviewed results"):
        st.download_button("Download research CSV", inspection_csv(leads), "company_research.csv", "text/csv")
        st.caption("Ownership and configuration approval are mocked. Approved JSON stays local until shared with the team.")
        if st.button("Prepare approved JSON handoff", disabled=active):
            st.session_state["handoff_" + run_id] = json.dumps(store.handoff(run_id), indent=2)
        handoff = st.session_state.get("handoff_" + run_id)
        if handoff:
            st.download_button("Download approved handoff", handoff, "approved_leads.json", "application/json")
        st.download_button("Download evaluation data", json.dumps(evaluation(store, run_id), indent=2), "evaluation.json", "application/json")


results_panel(selected)
