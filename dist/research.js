/* P2: research has its own provider status and review history. */
(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const uid = () => crypto.randomUUID();
  const labels = {worth_exploring:'Worth exploring', research_further:'Research further', defer:'Defer',
    relevant_signal:'Relevant signal', evidence_gap:'Evidence gap', service_mismatch:'Service mismatch',
    already_owned:'Existing relationship', timing:'Timing', duplicate:'Duplicate', other:'Other',
    opportunity:'This opportunity', theme:'This research theme', consultant:'This consultant', cgs:'CGS-wide'};
  const active = run => ['pending', 'running', 'stopping'].includes(run?.status);
  const statusNext = run => active(run) ? 'Research in progress' : ({
    completed:'Ready for evidence review',
    incomplete:'Incomplete — review gaps',
    paused:'Resume required',
    failed:'Failed — inspect and retry'
  }[run?.status] || 'Review saved task');
  const field = (label, name, value = '', type = 'text', required = true) => {
    const hints = name === 'domain'
      ? 'inputmode="url" autocapitalize="none" spellcheck="false" autocomplete="off"'
      : 'autocomplete="off"';
    return `<div class="form-field"><label for="r-${name}">${esc(label)}</label><input id="r-${name}" name="${name}" type="${type}" value="${esc(value)}" ${required?'required':''} ${hints} maxlength="${name==='company'?200:160}"></div>`;
  };
  const area = (label, name, value = '') => `<div class="form-field"><label for="r-${name}">${esc(label)}</label><textarea id="r-${name}" name="${name}" required maxlength="4000">${esc(value)}</textarea></div>`;
  const options = values => values.map(x => `<option value="${x}">${labels[x]}</option>`).join('');
  const list = values => values?.length ? `<ul>${values.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p>None recorded.</p>';
  let root, csrf, timer, epoch = 0, loading = false, theme, runs = [], opportunities = [], handoffs = [], result, openQualification, manager;
  let proposalContext, decidingProposal, loadAgain = false;
  let selectedRun = null, signatures = {}, sending = false, reviewRevision;

  async function request(path, body) {
    const response = await fetch('/api/research/' + path, body === undefined ? {cache:'no-store'} : {
      method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Research request failed.');
    return data.result;
  }

  function error(message, dialog = false) {
    const box = root?.querySelector(dialog ? '#r-dialog-error' : '#r-error');
    if (box) { box.hidden = !message; box.textContent = message; }
  }

  function update(id, value) {
    const element = root?.querySelector('#' + id);
    if (element && signatures[id] !== value) {
      const statusDetailsOpen = id === 'r-status' && element.querySelector('details')?.open;
      const focusedId = id === 'r-status' && element.contains(document.activeElement)
        ? document.activeElement.id : null;
      const focusedAction = id === 'r-status' && element.contains(document.activeElement)
        ? document.activeElement.closest('[data-r-action]')?.dataset.rAction : null;
      element.innerHTML = value;
      if (statusDetailsOpen) element.querySelector('details')?.setAttribute('open','');
      if (focusedAction) element.querySelector(`[data-r-action="${focusedAction}"]`)?.focus();
      else if (focusedId) {
        const replacement = document.getElementById(focusedId);
        if (replacement && element.contains(replacement)) replacement.focus();
      }
      signatures[id] = value;
    }
  }

  function evidence(card, ids) {
    const selected = new Set(ids || card.research.evidence.map(x => x.evidence_id));
    return card.research.evidence.filter(x => selected.has(x.evidence_id)).map(item => {
      const source = card.sources.find(x => x.source_id === item.source_id);
      let link = '';
      try {
        const url = new URL(source?.url);
        if (['https:', 'http:'].includes(url.protocol)) link = `<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">Open source ↗</a>`;
      } catch { /* Display an unavailable source without inventing a URL. */ }
      return `<article class="r-evidence"><small>${esc(item.evidence_id)} · ${esc(item.category)}</small>
        <p><strong>Observed fact:</strong> ${esc(item.observed_fact)}</p><blockquote>${esc(item.excerpt)}</blockquote>
        <p class="r-meta">Published: ${esc(item.publication_date || 'Unknown')} · Retrieved: ${esc(source?.retrieved_at || 'Unknown')}<br>${link || 'Source unavailable'} ${esc(source?.url || '')}</p></article>`;
    }).join('') || '<p>No cited evidence available.</p>';
  }

  function researchCard(card, index) {
    const r = card.research;
    return `<section class="panel r-card"><div class="panel-head"><h2>${esc(r.company)}</h2><span class="sample-label">${card.mode==='synthetic'?'SYNTHETIC SAMPLE':'PUBLIC RESEARCH'}</span></div>
      <div class="panel-body"><p><strong>${esc(card.review.decision)}</strong> · Human source review required</p><p class="r-meta">${esc(r.domain)} · Confidence: ${esc(r.confidence)} · Identity: ${esc(r.identity)} · Cohort: ${esc(r.eligibility)}<br>Research priority: ${esc(card.priority.band)} · Evidence coverage: ${Math.round(card.priority.coverage*100)}% · Qualification unassessed</p>
      <h3>Problem hypothesis</h3><p>${esc(r.problem_hypothesis)}</p><h3>Possible CGS relevance</h3><p>${esc(r.cgs_relevance)}</p>
      <h3>Alternative explanation</h3><p>${esc(r.alternative_explanation)}</p>
      <h3>Unknowns & next investigation</h3>${list(r.unanswered_questions)}<p>${esc(r.suggested_next_investigation)}</p>
      <details><summary>Contacts from public sources (${r.people?.length || 0})</summary>${r.people?.length ? r.people.map(p => `<p>${esc(p.name)} · ${esc(p.role)}<br><small>Role as of: ${esc(p.role_as_of || 'Unknown')} · Verified: ${esc(p.verified_at)} · Evidence: ${esc(p.evidence_ids.join(', '))}</small></p>`).join('') : '<p>No verified contact recorded. An opportunity may still be saved.</p>'}</details>
      <details><summary>Validation issues, gaps & conflicts</summary>${list([...card.validation_issues, ...(card.research_gaps || []), ...r.conflicts])}</details>
      <details><summary>Observed facts & source excerpts (${r.evidence.length})</summary>${evidence(card)}</details>
      <p class="notice">Public signals suggest an opportunity to investigate. Budget, buying authority and willingness to engage remain unconfirmed.</p>
      <button class="btn primary" data-r-action="create" data-index="${index}" ${active(result?.run)||!r.evidence.length?'disabled':''}>Save an opportunity…</button></div></section>`;
  }

  function opportunityCard(record) {
    const o = record.opportunity, lead = o.research_snapshot, latest = record.latest_feedback?.feedback;
    return `<article class="panel r-card" data-opportunity="${esc(o.opportunity_id)}"><div class="panel-head"><h2>${esc(o.title)}</h2><span class="badge">${esc(latest ? labels[latest.outcome] : 'Awaiting review')}</span></div>
      <div class="panel-body"><p class="r-meta">${esc(lead.research.company)} · Team: ${esc(o.team || 'Unknown')} · Revision ${record.revision}<br>Theme ${esc(lead.problem_brief_id)} / ${esc(lead.problem_brief_version)} · ${lead.mode==='synthetic'?'Synthetic sample':'Public research'}</p>
      <h3>Problem hypothesis</h3><p>${esc(o.problem_hypothesis)}</p><h3>CGS relevance hypothesis</h3><p>${esc(o.cgs_relevance_hypothesis)}</p><h3>Alternative explanation</h3><p>${esc(o.alternative_explanation)}</p>
      <details><summary>Cited facts & sources (${o.evidence_ids.length})</summary>${evidence(lead, o.evidence_ids)}</details>
      <details><summary>Review history (${record.feedback_history.length})</summary>${record.feedback_history.map(x => `<div class="r-history"><strong>${esc(labels[x.feedback.outcome])}</strong> · ${esc(x.feedback.consultant_id)}<p>${esc(labels[x.feedback.reason_code])} · ${esc(labels[x.feedback.scope])}<br>${esc(x.feedback.comment || 'No additional comment')}</p><small>${esc(x.feedback.recorded_at)} · Review time: ${x.feedback.review_minutes===null?'Not recorded':esc(x.feedback.review_minutes)+' min'} · Sources checked: ${x.source_support_checked?'Yes':'No'}</small></div>`).join('') || '<p>No human review recorded.</p>'}</details>
      ${record.handoff?.status==='accepted'?`<button class="btn primary" data-r-action="open-case" data-id="${esc(record.handoff.case_id)}" ${record.handoff.case_available?'':'disabled'}>Open qualification case</button>`:`<button class="btn primary" data-r-action="review" data-id="${esc(o.opportunity_id)}">Record human review…</button>`}
      ${record.handoff?.status==='pending_acceptance'?`<button class="btn" data-r-action="decide" data-id="${esc(record.handoff.handoff_id)}">Review handoff…</button>`:record.handoff?.status!=='accepted'&&latest?.outcome==='worth_exploring'?`<button class="btn" data-r-action="handoff" data-id="${esc(o.opportunity_id)}">Submit for acceptance</button>`:''}
      <p class="r-meta">Handoff: ${esc(record.handoff?.status || 'Not requested')}${record.handoff?.reason?' · '+esc(record.handoff.reason):''}</p></div></article>`;
  }

  function handoffCard(item) {
    const r=item.receipt, o=item.snapshot.opportunity;
    return `<article class="panel r-card"><div class="panel-body"><h3>${esc(o.title)}</h3><p>${esc(o.research_snapshot.research.company)} · ${esc(r.status)}</p><p class="r-meta">Opportunity revision ${r.opportunity_revision} · Submitted ${esc(item.submitted_at)}${r.reviewer?'<br>Reviewed by '+esc(r.reviewer):''}</p>
      ${r.reason?`<p>${esc(r.reason)}</p>`:''}${r.owner_id?`<p>Owner: ${esc(r.owner_id)}<br>Next: ${esc(r.next_action)} · Due ${esc(r.due_date)}</p>`:''}
      ${r.status==='pending_acceptance'?`<button class="btn primary" data-r-action="decide" data-id="${esc(r.handoff_id)}">Review handoff…</button>`:r.status==='accepted'?`<button class="btn" data-r-action="open-case" data-id="${esc(r.case_id)}" ${item.case_available?'':'disabled'}>Open qualification case</button>`:''}</div></article>`;
  }

  function paint() {
    if (!root) return;
    root.classList.toggle('has-run', !!result?.run);
    const displayedLimits = theme?.research.simulated ? theme?.limits : theme?.discovery_limits;
    update('r-configuration', theme ? `<strong>Research: ${theme.research.simulated?'Offline synthetic fixtures':theme.research.configured?'Credentials present; connection unverified':'Not configured'}</strong>
      <p class="r-direction-lead">${esc(theme.brief.capability)}</p>
      <details class="r-run-details"><summary>View full direction, model & limits</summary><p>${esc(theme.brief.problem)}</p><small>${esc(theme.brief.brief_id)} / ${esc(theme.brief.version)} · Approval: ${esc(theme.brief.approval_basis)}<br>Model: ${esc(theme.research.model || 'Not set')}<br>Limits: ${displayedLimits.searches} searches · ${displayedLimits.extractions} extracts · ${displayedLimits.model_calls} model calls</small></details>
      ${!theme.research.configured?'<p class="notice">Configure Gemini and Tavily in the server .env, then restart. Starting research is unavailable until configured.</p>':''}` : 'Loading research configuration…');
    const start = root.querySelector('#r-start');
    const mode = root.querySelector('#r-mode');
    if (mode) {
      mode.disabled = !!theme?.research.simulated || sending;
      if (theme?.research.simulated) mode.value = 'company';
      const known = mode.value === 'company';
      for (const name of ['company','domain']) {
        const input = root.querySelector('#r-'+name);
        input.required = known; input.disabled = !known;
        input.closest('.form-field').hidden = !known;
      }
    }
    update('r-start-help', theme?.research.simulated ? 'Choose a fictional company below. Offline fixtures; no external API calls. One active task at a time.' : 'Discover up to 2 briefs from the direction above, or investigate one known company. Each run is bounded to 18 model calls, 10 searches and 12 page reads. Missing evidence stays visible; human review is required.');
    update('r-demo-companies', (theme?.demo_companies||[]).map(c=>`<button class="btn" type="button" data-r-action="demo-company" data-company="${esc(c.company)}" data-domain="${esc(c.domain)}">Use ${esc(c.company)}</button>`).join(''));
    if (start) start.disabled = !theme?.research.configured || runs.some(active) || sending;
    update('r-jobs', runs.length ? runs.map(run => `<button class="r-job ${selectedRun===run.run_id?'selected':''}" data-r-action="select" data-id="${esc(run.run_id)}"><strong>${esc(run.company.company)}</strong><span>${esc(run.status)} · ${esc(run.created_at.slice(0,16).replace('T',' '))}</span></button>`).join('') : '<p>No research tasks yet.</p>');
    const run = result?.run;
    update('r-status-announcement', run ? `${esc(run.company.company)} research ${esc(run.status)}.` : 'No research task selected.');
    update('r-status', run ? `<div class="r-status-heading"><h2>${esc(run.company.company)} <span class="badge">${esc(run.status)}</span></h2><span class="r-status-next">${statusNext(run)}</span></div>
      <div class="r-run-summary"><span><strong>${run.counters.searches}/${run.limits.searches}</strong> searches</span><span><strong>${run.counters.extractions}/${run.limits.extractions}</strong> page reads</span><span><strong>${run.counters.model_calls}/${run.limits.model_calls}</strong> model calls</span></div>
      ${run.error?`<div class="${run.status==='completed'?'notice':'error-box'}">${esc(run.error)}</div>`:''}<div class="run-actions">${active(run)?`<button class="btn" data-r-action="stop" ${run.status==='stopping'?'disabled':''}>${run.status==='stopping'?'Stopping at next checkpoint…':'Stop research'}</button>`:''}${run.resume_available?'<button class="btn" data-r-action="resume">Resume saved task</button>':''}<button class="btn" data-r-action="refresh">Refresh status</button></div>
      <details class="r-run-details"><summary id="r-run-details-summary">Run details & recent progress</summary><p class="r-meta">Run ${esc(run.run_id)}<br>Theme ${esc(run.theme_id)} / ${esc(run.theme_version)} · Approval: ${esc(run.approval_basis)}<br>Active ${Math.round(run.active_seconds)}s / ${run.limits.active_seconds}s · Credits reserved ${run.reserved_credits}/${run.limits.tavily_credits}</p>${run.events.map(x => `<p><small>${esc(x.at)}</small><br>${esc(x.message)}</p>`).join('') || '<p>Waiting for first progress event.</p>'}</details>` : '<div class="r-empty-status"><h2>No task selected</h2><p>Start a bounded research task or choose a saved task to review its evidence.</p></div>');
    update('r-queue', run?.candidate_queue?.length ? `<h3>Candidate research queue</h3><p>Stage: ${esc(run.stage)} · Briefs ready for review: ${run.briefs_ready || 0}</p>${run.candidate_queue.map(c=>`<article class="r-history"><strong>${esc(c.company)}</strong> · ${esc(c.status)}<p>${esc(c.disposition || 'Research in progress')}</p>${c.quality_gaps?.length?list(c.quality_gaps):''}</article>`).join('')}` : '');
    update('r-results', result ? result.company_cards.map(researchCard).join('') || `<div class="panel panel-body"><p>${active(run)?'Research is running. Results will appear after evidence validation.':'No reviewable brief was saved. Inspect the candidate queue and evidence gaps.'}</p></div>` : '');
    update('r-notes', (result?.candidate_notes || []).filter(n=>n.evidence.length).map(n=>`<section class="panel panel-body"><h3>${esc(n.company)}: preliminary research</h3><p>${esc(n.disposition || 'Evidence review in progress.')}</p><p class="notice">This is not a ready brief. These model-reviewed excerpts preserve useful research while gaps remain.</p><details><summary>Available facts and sources (${n.evidence.length})</summary>${evidence({research:{evidence:n.evidence},sources:n.sources})}</details></section>`).join(''));
    update('r-opportunities', opportunities.length ? opportunities.map(opportunityCard).join('') : '<div class="panel panel-body"><p>No opportunities saved yet. Open a completed company card and save a specific opportunity.</p></div>');
    update('r-handoffs', handoffs.length ? handoffs.map(handoffCard).join('') : '<div class="panel panel-body"><p>No handoffs yet. Submit a reviewed opportunity for explicit pipeline acceptance.</p></div>');
    paintManager();
  }

  const percentage = value => value===null?'Not measured':Math.round(value*100)+'%';
  const portion = (count, total, value) => total ? `${count} of ${total} · ${percentage(value)}` : 'Not measured';
  function paintManager() {
    if(!manager) return;
    const k=manager.kpis;
    update('r-manager-kpis', `<p class="notice">${esc(manager.metric_cohort)} These workflow measures do not establish sales conversion or lead quality.</p>
      <div class="r-manager-metrics">${[[k.opportunities,'Opportunities'],[k.reviewed_opportunities,'Reviewed opportunities'],[portion(k.worth_exploring,k.reviewed_opportunities,k.worth_exploring_rate),'Worth exploring / reviewed'],[k.accepted_handoffs,'Accepted handoffs'],[portion(k.recorded_replies,k.recorded_outreach,k.recorded_response_rate),'Recorded replies / outreach'],[k.review_minutes_median===null?'Not recorded':k.review_minutes_median+' min','Median review time']].map(([v,l])=>`<div><strong>${esc(v)}</strong><small>${esc(l)}</small></div>`).join('')}</div>
      <p class="r-meta">Synthetic opportunities: ${k.synthetic_opportunities} · Review-time samples: ${k.review_minutes_samples} · Recorded outreach: ${k.recorded_outreach} · Recorded replies: ${k.recorded_replies}<br>Research tasks: ${manager.research_runs.length} · Paused: ${manager.research_runs.filter(r=>r.status==='paused').length}<br>AI review: ${manager.configuration.simulated?'Simulated feedback analysis':manager.configuration.configured?'Credentials present; connection unverified':'Not configured'} · One model call per review.</p>`);
    update('r-manager-feedback', `<p class="r-meta">Proposal base: ${esc(manager.brief.version)} · Scope: ${esc(manager.scope)} · Consultant: ${esc(manager.consultant_id)}. Only the latest feedback in this scope and version is eligible.</p>
      ${manager.eligible_feedback.length?`<fieldset class="r-evidence-choices"><legend>Feedback to review (up to 20)</legend>${manager.eligible_feedback.slice(0,20).map(f=>`<label><input type="checkbox" name="manager_feedback" value="${esc(f.feedback_id)}" checked><span>${esc(labels[f.outcome])} · ${esc(f.consultant_id)} · ${esc(labels[f.reason_code])}<br>${esc(f.comment)}<br><small>${esc(f.feedback_id)}</small></span></label>`).join('')}</fieldset>`:'<p>No eligible feedback. Record a research review with this feedback scope before proposing a direction change.</p>'}`);
    const ai=root.querySelector('#r-analyze-feedback'), manual=root.querySelector('#r-manual-proposal');
    ai.textContent=manager.configuration.simulated?'Simulate feedback review':'Analyze feedback with AI';
    ai.disabled=!manager.configuration.configured || !manager.eligible_feedback.length || manager.reviews.some(r=>r.status==='running') || sending;
    manual.disabled=!manager.eligible_feedback.length || sending;
    update('r-manager-reviews', manager.reviews.map(r=>`<div class="r-history"><strong>AI review · ${esc(r.status)}</strong><p class="r-meta">${esc(r.model)} · ${r.calls}/1 calls · ${esc(r.created_at)}</p>${r.error?`<p class="error-box">${esc(r.error)}</p>`:''}${r.result?`<p>${esc(r.result.summary)}</p><p class="r-meta">${r.result.proposal_id?'A pending proposal was saved for human review.':'No direction change was proposed.'}</p>`:''}</div>`).join('') || '<p>No AI feedback reviews yet.</p>');
    update('r-manager-proposals', manager.proposals.map(item=>{
      const p=item.proposal;
      return `<article class="panel r-card"><div class="panel-body"><h3>Direction proposal · ${esc(p.status)}</h3><p>${esc(p.rationale)}</p><p class="r-meta">Origin: ${esc(item.origin)} · Scope: ${esc(p.scope)} · Consultant: ${esc(p.consultant_id)}<br>Base: ${esc(p.base_theme_version)} · Feedback records: ${p.feedback_ids.length}</p>
        <details><summary>Proposed changes: before → after</summary><pre class="r-json">${esc(JSON.stringify(item.diff,null,2))}</pre></details>
        ${p.status==='pending_review'?`<button class="btn primary" data-r-action="direction-decision" data-id="${esc(p.proposal_id)}">Approve / reject proposal…</button>`:`<p>Reviewed by ${esc(p.reviewer)} · ${esc(p.review_reason)}<br>Resulting version: ${esc(p.resulting_theme_version||'No change')}</p>`}</div></article>`;
    }).join('') || '<p>No direction proposals yet.</p>');
    update('r-manager-versions', `<details><summary>Approved direction memory (${manager.versions.length} versions)</summary>${manager.versions.map(v=>`<div class="r-history"><strong>${esc(v.version)}</strong><p>${esc(v.scope)} · ${esc(v.consultant_id||'Shared theme')} · Parent ${esc(v.parent_version||'Imported baseline')}<br>Approved by ${esc(v.brief.approved_by)} · Basis ${esc(v.brief.approval_basis)}</p></div>`).join('')}</details>`);
  }

  function selectedFeedback() {
    const ids=[...root.querySelectorAll('input[name="manager_feedback"]:checked')].map(x=>x.value);
    if(!ids.length) throw new Error('Select feedback before requesting a proposal.');
    return {consultant_id:manager.consultant_id,scope:manager.scope,base_theme_version:manager.brief.version,feedback_ids:ids};
  }

  async function load() {
    if (!root) return;
    if (loading) { loadAgain=true; return; }
    loading = true;
    const generation = epoch;
    const selection = selectedRun;
    let reloadSelection = false;
    try {
      const consultant=root.querySelector('#r-consultant')?.value.trim() || '';
      const managerConsultant=root.querySelector('#r-manager-consultant').value.trim() || 'local-consultant';
      const scope=root.querySelector('#r-manager-scope').value;
      const [nextTheme, nextRuns, nextOpportunities, nextHandoffs, nextManager] = await Promise.all([
        request('theme?consultant_id='+encodeURIComponent(consultant)), request('runs'), request('opportunities'), request('handoffs'),
        request('orchestrator?consultant_id='+encodeURIComponent(managerConsultant)+'&scope='+scope)
      ]);
      const selected = selectedRun && nextRuns.some(x => x.run_id===selectedRun) ? selectedRun : nextRuns[0]?.run_id;
      const nextResult = selected ? await request('runs/' + selected + '/results') : null;
      if (generation!==epoch || !root) return;
      if (selection!==selectedRun) { reloadSelection=true; return; }
      theme=nextTheme; runs=nextRuns; opportunities=nextOpportunities; handoffs=nextHandoffs; manager=nextManager; selectedRun=selected; result=nextResult;
      paint(); error('');
    } catch (e) { if (generation===epoch) error(e.message); }
    finally {
      if (generation===epoch) {
        loading=false;
        if (reloadSelection || loadAgain) { loadAgain=false; load(); }
      }
    }
  }

  function intent(key, body) {
    const serialized = JSON.stringify(body);
    let saved;
    try { saved = JSON.parse(localStorage.getItem(key)); } catch { /* No valid saved request. */ }
    if (!saved || saved.input!==serialized) {
      saved = {input:serialized, id:uid()};
      localStorage.setItem(key, JSON.stringify(saved));
    }
    return {request_id:saved.id, ...body};
  }

  function dialog(title, content) {
    const element = root.querySelector('#r-dialog');
    element.innerHTML = `<div class="modal-head"><h2>${esc(title)}</h2><button class="btn" data-r-action="close" aria-label="Close research dialog">Close</button></div><div class="modal-body"><div id="r-dialog-error" class="error-box" role="alert" hidden></div>${content}</div>`;
    element.showModal();
  }

  function create(index) {
    const card = result.company_cards[index], r = card.research;
    dialog('Save an opportunity', `<p class="notice">Keep one specific problem or team per opportunity. The saved card and citations are retained as a snapshot; another opportunity can use the same company.</p>
      <form id="r-opportunity-form" data-index="${index}">${field('Opportunity title','title',r.company+' — research opportunity')}${field('Team / business unit (optional)','team','', 'text', false)}
      ${area('Problem hypothesis','problem_hypothesis',r.problem_hypothesis)}${area('CGS relevance hypothesis','cgs_relevance_hypothesis',r.cgs_relevance)}${area('Alternative explanation','alternative_explanation',r.alternative_explanation)}
      <fieldset class="r-evidence-choices"><legend>Supporting facts — select at least one</legend>${r.evidence.map(e => `<label><input type="checkbox" name="evidence_ids" value="${esc(e.evidence_id)}" checked><span>${esc(e.evidence_id)} · ${esc(e.observed_fact)}</span></label>`).join('')}</fieldset>
      <button class="btn primary" type="submit">Save opportunity</button></form>`);
  }

  function review(id) {
    const record = opportunities.find(x => x.opportunity.opportunity_id===id);
    reviewRevision = record.revision;
    dialog('Record human research review', `<p class="notice">This records a research judgment. Feedback is saved for future analysis; it does not change the research theme or create a qualification case.</p>
      <form id="r-review-form" data-id="${esc(id)}">${field('Reviewer / consultant','reviewer',localStorage.getItem('cgs-reviewer') || '')}
      <div class="form-field"><label for="r-outcome">Decision</label><select id="r-outcome" name="outcome">${options(['research_further','worth_exploring','defer'])}</select></div>
      <div class="form-row"><div class="form-field"><label for="r-reason">Reason</label><select id="r-reason" name="reason_code">${options(['evidence_gap','relevant_signal','service_mismatch','already_owned','timing','duplicate','other'])}</select></div>
      <div class="form-field"><label for="r-scope">Feedback applies to</label><select id="r-scope" name="scope">${options(['opportunity','theme','consultant','cgs'])}</select></div></div>
      <div class="form-field"><label for="r-comment">Explanation (required for Other)</label><textarea id="r-comment" name="comment" maxlength="4000"></textarea></div>
      <div class="form-field"><label for="r-minutes">Review time in minutes (optional)</label><input id="r-minutes" name="review_minutes" type="number" min="0" step="0.1"></div>
      <label class="confirm-check"><input type="checkbox" name="source_support_checked">I checked the cited facts against their sources (required for Worth exploring).</label>
      <details><summary>Review cited sources</summary>${evidence(record.opportunity.research_snapshot,record.opportunity.evidence_ids)}</details>
      <button class="btn primary" type="submit">Save review</button></form>`);
  }

  async function decide(id) {
    const item=await request('handoffs/'+id), receipt=item.receipt, o=item.snapshot.opportunity;
    if(receipt.status!=='pending_acceptance') { await load(); throw new Error('This handoff already has a decision.'); }
    const people=o.research_snapshot.research.people || [];
    dialog('Review research handoff', `<p class="notice">${esc(o.title)} · ${esc(o.research_snapshot.research.company)}<br>Acceptance creates one Not contacted case. Public research remains separate from confirmed qualification evidence.</p>
      <details open><summary>Research hypotheses & cited sources</summary><p>${esc(o.problem_hypothesis)}</p><p>${esc(o.cgs_relevance_hypothesis)}</p><p>Alternative: ${esc(o.alternative_explanation)}</p>${evidence(o.research_snapshot,o.evidence_ids)}</details>
      <h3>Existing cases to check (${item.duplicate_candidates.length})</h3>${item.duplicate_candidates.map(c=>`<p>${esc(c.company)} · ${esc(c.project)} · ${esc(c.owner)} · ${esc(c.stage)}</p>`).join('')||'<p>No matching company/domain found in local cases. Check known relationships as well.</p>'}
      <form id="r-handoff-form" data-id="${esc(id)}" data-revision="${receipt.opportunity_revision}">${field('Receiving reviewer','receiving_reviewer',localStorage.getItem('cgs-reviewer')||'')}
      <div class="form-field"><label for="r-decision">Decision</label><select id="r-decision" name="status" required><option value="" selected disabled>Choose a decision…</option><option value="accepted">Accept into qualification</option><option value="needs_research">Return for research</option><option value="rejected">Reject handoff</option></select></div>
      <fieldset id="r-accept-fields" class="r-evidence-choices"><legend>Acceptance details</legend>${field('Accountable owner','owner_id')}${area('Next action','next_action',people.length?'Verify contact and prepare an introduction':'Identify and verify an appropriate contact')}${field('Next-action due date (system date)','due_date','','date')}
      <div class="form-field"><label for="r-handoff-contact">Public research contact (optional)</label><select id="r-handoff-contact" name="contact_index"><option value="">Unknown — assign contact research to the owner</option>${people.map((p,i)=>`<option value="${i}">${esc(p.name)} · ${esc(p.role)}</option>`).join('')}</select></div>
      <label class="confirm-check"><input name="relationship_checked" type="checkbox" required>I checked existing cases and known relationships; this is a distinct opportunity.</label>
      ${area('Relationship / duplicate-check conclusion','relationship_note')}</fieldset>
      <div class="form-field"><label for="r-decision-reason">Decision reason (required for return / reject)</label><textarea id="r-decision-reason" name="reason" maxlength="4000"></textarea></div><button class="btn primary" type="submit">Save receiving decision</button></form>`);
    const decision=root.querySelector('#r-decision');
    const syncDecision=()=>{
      root.querySelector('#r-accept-fields').disabled=decision.value!=='accepted';
      root.querySelector('#r-decision-reason').required=!!decision.value&&decision.value!=='accepted';
    };
    decision.addEventListener('change',syncDecision);syncDecision();
  }

  async function submit(event) {
    const form = event.target;
    if (!['r-start-form','r-opportunity-form','r-review-form','r-handoff-form','r-direction-form','r-direction-decision-form'].includes(form.id)) return;
    event.preventDefault();
    if (sending) return;
    const values = new FormData(form), inDialog=form.id!=='r-start-form';
    sending = true;
    form.querySelectorAll('button').forEach(x => x.disabled=true);
    error('',inDialog);
    try {
      let body, path, key;
      if (form.id==='r-start-form') {
        body={consultant_id:values.get('consultant').trim(), mode:theme.research.simulated?'company':values.get('mode'), research_version:theme.research.simulated?'legacy':'v2', theme_id:theme.brief.brief_id,theme_version:theme.brief.version};
        if(body.mode==='company') body.company={company:values.get('company').trim(),domain:values.get('domain').trim()};
        path='runs'; key='cgs-research-intent';
      } else if (form.id==='r-opportunity-form') {
        const card=result.company_cards[Number(form.dataset.index)];
        body={run_id:card.run_id, lead_id:card.lead_id, title:values.get('title').trim(),team:values.get('team').trim() || null,
          problem_hypothesis:values.get('problem_hypothesis').trim(),cgs_relevance_hypothesis:values.get('cgs_relevance_hypothesis').trim(),alternative_explanation:values.get('alternative_explanation').trim(),evidence_ids:values.getAll('evidence_ids')};
        path='opportunities'; key='cgs-opportunity-intent';
      } else if (form.id==='r-direction-form') {
        body={...proposalContext,patch:JSON.parse(values.get('patch_json')),rationale:values.get('rationale').trim()};
        path='orchestrator/proposals'; key='cgs-manual-direction-proposal';
      } else if (form.id==='r-direction-decision-form') {
        body={status:values.get('status'),reviewer:values.get('proposal_reviewer').trim(),reason:values.get('reason').trim()};
        path='orchestrator/proposals/'+decidingProposal+'/decision'; key='cgs-direction-decision:'+decidingProposal;
      } else if (form.id==='r-handoff-form') {
        const accept=values.get('status')==='accepted';
        body={opportunity_revision:Number(form.dataset.revision),status:values.get('status'),reviewer:values.get('receiving_reviewer').trim(),reason:values.get('reason').trim(),
          owner_id:accept?values.get('owner_id').trim():null,next_action:accept?values.get('next_action').trim():null,due_date:accept?values.get('due_date'):null,
          relationship_checked:accept&&values.has('relationship_checked'),relationship_note:accept?values.get('relationship_note').trim():'',
          contact_index:accept&&values.get('contact_index')!==''?Number(values.get('contact_index')):null};
        path='handoffs/'+form.dataset.id+'/decision'; key='cgs-handoff-decision:'+form.dataset.id;
      } else {
        body={revision:reviewRevision, consultant_id:values.get('reviewer').trim(),outcome:values.get('outcome'),reason_code:values.get('reason_code'),scope:values.get('scope'),comment:values.get('comment').trim(),review_minutes:values.get('review_minutes')===''?null:Number(values.get('review_minutes')),source_support_checked:values.has('source_support_checked')};
        path='opportunities/'+form.dataset.id+'/feedback'; key='cgs-feedback-intent:'+form.dataset.id;
      }
      const saved=await request(path,intent(key,body));
      localStorage.removeItem(key);
      if (form.id==='r-start-form') selectedRun=saved.run_id;
      if (form.id==='r-review-form') localStorage.setItem('cgs-reviewer',body.consultant_id);
      if (inDialog) root.querySelector('#r-dialog').close();
      await load();
    } catch (e) { error(e.message,inDialog); }
    finally { sending=false; form.querySelectorAll('button').forEach(x => x.disabled=false); paint(); }
  }

  async function click(event) {
    const button=event.target.closest('[data-r-action]');
    if (!button || sending) return;
    try {
      switch(button.dataset.rAction) {
        case 'demo-company':
          root.querySelector('#r-consultant').value='Demo consultant';
          root.querySelector('#r-company').value=button.dataset.company;
          root.querySelector('#r-domain').value=button.dataset.domain;
          await load(); break;
        case 'close': root.querySelector('#r-dialog').close(); break;
        case 'create': create(Number(button.dataset.index)); break;
        case 'review': review(button.dataset.id); break;
        case 'manager-refresh': await load(); break;
        case 'analyze-feedback': {
          const body=selectedFeedback(), key='cgs-ai-feedback-review';
          sending=true; paintManager();
          try { await request('orchestrator/reviews',intent(key,body)); localStorage.removeItem(key); await load(); }
          finally { sending=false; paintManager(); }
          break;
        }
        case 'manual-proposal':
          proposalContext=selectedFeedback();
          dialog('Create a manual direction proposal', `<p class="notice">This is a manual proposal, not an AI result. It still requires a separate human approval. Allowed fields: problem, signals, buyer_roles, exclusions, excluded_domains.</p><form id="r-direction-form">${area('Direction patch (JSON)','patch_json',JSON.stringify({signals:manager.brief.signals},null,2))}${area('Feedback-based rationale','rationale')}<button class="btn primary" type="submit">Save pending proposal</button></form>`);
          break;
        case 'direction-decision': {
          const item=manager.proposals.find(x=>x.proposal.proposal_id===button.dataset.id);
          decidingProposal=button.dataset.id;
          dialog('Human direction decision', `<p class="notice">Approval creates a new version for future tasks. Existing tasks keep their snapshots. Local approval preserves the existing mock/CGS approval basis.</p><pre class="r-json">${esc(JSON.stringify(item.diff,null,2))}</pre><form id="r-direction-decision-form">${field('Approving reviewer','proposal_reviewer',localStorage.getItem('cgs-reviewer')||'')}<div class="form-field"><label for="r-proposal-status">Decision</label><select id="r-proposal-status" name="status" required><option value="" selected disabled>Choose a decision…</option><option value="approved">Approve new direction version</option><option value="rejected">Reject proposal</option></select></div>${area('Decision rationale','reason')}<button class="btn primary" type="submit">Save direction decision</button></form>`);
          break;
        }
        case 'decide': await decide(button.dataset.id); break;
        case 'open-case': await openQualification(button.dataset.id); break;
        case 'handoff': {
          button.disabled=true;
          const record=opportunities.find(x=>x.opportunity.opportunity_id===button.dataset.id);
          const key='cgs-handoff-submit:'+button.dataset.id;
          await request('opportunities/'+button.dataset.id+'/handoff',intent(key,{revision:record.revision}));
          localStorage.removeItem(key); await load(); break;
        }
        case 'select': selectedRun=button.dataset.id; await load(); break;
        case 'refresh': await load(); break;
        case 'stop': case 'resume':
          button.disabled=true;
          await request('runs/'+selectedRun+'/'+button.dataset.rAction,{}); await load(); break;
      }
    } catch(e) { error(e.message); button.disabled=false; }
  }

  window.CGSResearch = {
    page: () => '<div id="research-root"></div>',
    caseContext(caseRecord) {
      const snapshot=caseRecord.researchContext;
      if(!snapshot) return '';
      const o=snapshot.opportunity;
      return `<section class="panel r-card r-case-context"><div class="panel-head"><h2>Research handoff background</h2></div><div class="panel-body"><p class="notice">Public research hypotheses, not confirmed qualification evidence. This case uses system dates; demo sample dates do not apply.</p>
        <p>Team: ${esc(o.team||'Unknown')} · Contact: ${esc(caseRecord.contact||'Not identified')}<br>Received by ${esc(caseRecord.researchHandoff.receipt.reviewer)} · Opportunity revision ${snapshot.revision}</p>
        <h3>Problem hypothesis</h3><p>${esc(o.problem_hypothesis)}</p><h3>CGS relevance hypothesis</h3><p>${esc(o.cgs_relevance_hypothesis)}</p><h3>Alternative explanation</h3><p>${esc(o.alternative_explanation)}</p>
        <details><summary>Public facts & source excerpts</summary>${evidence(o.research_snapshot,o.evidence_ids)}</details></div></section>`;
    },
    mount(config) {
      openQualification=config.openCase;
      const next=config.active ? document.querySelector('#research-root') : null;
      if (next===root) { csrf=config.csrf; return; }
      clearInterval(timer); epoch++; loading=false; loadAgain=false; root=next; csrf=config.csrf; signatures={};
      if (!root) return;
      root.innerHTML=`<div class="page-heading research-page-heading"><div><h1>Find evidence for—or against—a conversation.</h1><p class="subtitle">Research a company, verify public facts, and keep every human decision explicit.</p></div></div>
        <nav class="research-journey" aria-label="Research workflow"><a href="#research-start"><span>1</span><strong>Start research</strong><small>Choose a scope</small></a><a href="#research-evidence"><span>2</span><strong>Review evidence</strong><small>Facts before fit</small></a><a href="#research-opportunities"><span>3</span><strong>Decide opportunity</strong><small>Human review</small></a><a href="#research-handoffs"><span>4</span><strong>Accept handoff</strong><small>Named owner</small></a><a class="research-governance-link" href="#research-governance"><strong>Manage direction</strong><small>Future tasks only</small></a></nav>
        <div id="r-error" class="error-box" role="alert" hidden></div><div id="r-status-announcement" class="sr-only" role="status" aria-live="polite"></div><section class="research-section"><div class="r-layout"><aside class="side-stack">
        <section id="research-start" class="panel r-start-panel"><div class="panel-head"><div><h2>Start research</h2><p class="r-panel-help">Run one bounded task at a time.</p></div></div><form id="r-start-form" class="panel-body">${field('Consultant','consultant',localStorage.getItem('cgs-reviewer') || '')}<div class="form-field"><label for="r-mode">Research mode</label><select name="mode" id="r-mode"><option value="discovery">Discover from research direction</option><option value="company">Investigate a known company</option></select></div>${field('Company name','company')}${field('Public website domain','domain')}<button class="btn primary" type="submit" id="r-start" disabled>Start research</button><p class="r-meta" id="r-start-help"></p><div id="r-demo-companies" class="run-actions"></div></form></section>
        <section class="panel"><div class="panel-head"><h2>Saved research tasks</h2></div><div class="panel-body" id="r-jobs"></div></section>
        <section class="panel r-direction-panel"><div class="panel-head"><h2>Research direction</h2></div><div class="panel-body" id="r-configuration"></div></section></aside>
        <div class="side-stack r-main" id="research-evidence"><section class="panel r-status-panel"><div class="panel-body" id="r-status"></div><div class="panel-body" id="r-queue"></div></section><div id="r-results"></div><div id="r-notes"></div></div></div></section>
        <section id="research-opportunities" class="research-section"><div class="page-heading r-opportunities-heading"><div><h2>Research opportunities</h2><p class="subtitle">Turn a supported hypothesis into a specific, reviewable opportunity.</p></div><button class="btn" data-r-action="refresh">Refresh opportunities</button></div><div id="r-opportunities" class="r-opportunity-grid"></div></section>
        <section id="research-handoffs" class="research-section"><div class="page-heading r-opportunities-heading"><div><h2>Handoff inbox & receipts</h2><p class="subtitle">Submission is not acceptance. A named reviewer decides ownership and next action.</p></div></div><div id="r-handoffs" class="r-opportunity-grid"></div></section><dialog id="r-dialog" aria-label="Research opportunity and review"></dialog>`;
      root.insertAdjacentHTML('beforeend',`<section id="research-governance" class="panel r-manager research-section"><div class="panel-head"><div><h2>Manage research direction</h2><p class="r-panel-help">Governance for future research tasks—not the next step for every lead.</p></div></div><div class="panel-body"><p class="description">Review workflow measures, scope feedback, and propose a controlled research-direction change. Every change needs human approval.</p><div id="r-manager-kpis"></div><div class="form-row">${field('Manager consultant context','manager-consultant',localStorage.getItem('cgs-reviewer')||'local-consultant')}<div class="form-field"><label for="r-manager-scope">Feedback scope</label><select id="r-manager-scope" name="manager_scope"><option value="theme">Shared research theme</option><option value="consultant">Personal consultant direction</option><option value="cgs">CGS-wide feedback for this theme</option></select></div></div><button class="btn" data-r-action="manager-refresh">Load manager context</button><div id="r-manager-feedback"></div><div class="run-actions"><button id="r-analyze-feedback" class="btn primary" data-r-action="analyze-feedback" disabled>Analyze feedback with AI</button><button id="r-manual-proposal" class="btn" data-r-action="manual-proposal" disabled>Create manual proposal…</button></div><div id="r-manager-reviews"></div><h3>Direction proposals</h3><div id="r-manager-proposals" class="r-opportunity-grid"></div><div id="r-manager-versions"></div></div></section>`);
      root.addEventListener('submit',submit); root.addEventListener('click',click);
      root.querySelector('#r-consultant').addEventListener('change',load);
      root.querySelector('#r-mode').addEventListener('change',paint);
      root.querySelector('#r-dialog').addEventListener('cancel',e => { if(sending)e.preventDefault(); });
      paint(); load(); timer=setInterval(() => { if ((runs.some(active) || manager?.reviews.some(r=>r.status==='running')) && !sending) load(); },2000);
    }
  };
})();
