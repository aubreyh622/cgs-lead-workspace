# CGS Lead Workspace

A prototype of AI agents that help CGS Advisors consultants find, contact and qualify new leads, built by Ivey MSc
AFP Team 15. It covers the front of the sales funnel, **Identified → Contacted → Active → Qualification**, and keeps a
person in charge of every decision that matters: agents research and prepare, people approve.

**Status: prototype, synthetic data only.** It is not connected to any CGS system. The Insightly connection is tested
on the team's own Insightly test account with invented companies. Lead quality and CGS acceptance are not yet verified.

## The workflow

| Step | What happens | Who |
|---|---|---|
| 1 Discover | Finds companies in recent news that match CGS's research direction (the problems CGS solves) | Discovery agent, with a direction a person approves |
| 2–3 Research | Writes a brief per company: dated, quoted facts with sources, why now, why CGS, unknowns, a contact only if a source names one | Research agent |
| 4 Accept | A consultant accepts (owner, rating, next step), returns with questions, or rejects with a reason. Reasons feed back into the direction | Person |
| 5 Contact | Drafts the first email, follow-ups and reconnect emails that cite the brief; the person sees the exact email and sends it | Outreach agent drafts, person sends |
| 6 Converse | Labels replies; reads call notes and proposes BANTHE scores, conditions and blockers, each with a word-for-word quote | Reply classifier, evidence agent; person accepts or edits |
| 7 Assess | The rules engine says whether the opportunity is Ready; the next-step agent suggests what to do about the gaps | Rules (no AI), next-step agent |
| 8 Approve | The qualification pack goes to an approver; approved means Qualification | Person |

A **manager agent** watches the team: a daily look over every open opportunity, a close look at one when something
happens to it, a memory per opportunity, and one message per owner. Agents also work on their own (a daily patrol,
handoffs between agents), but everything they prepare goes to a person's inbox first.

## Ground rules

- **Agents only read and prepare.** Sending an email, changing a score or writing to the CRM happens only after a person
  clicks. Agents never guess an email address.
- **Code checks the AI.** Quotes must match the source word for word, cited facts must exist, drafts may not promise
  prices or results, and a failed AI call is reported, never replaced by made-up results.
- **One gate to the outside world.** Every outside system goes through `api/toolkit/`, with a permission table (which
  agent may use what), a usage log and daily limits.
- **Synthetic data for development.** A synthetic internet (`config/search.json`, provider `mock`) lets the whole
  workflow run with real AI and no real company. Client data reaches an AI model only when `CLIENT_DATA_TO_AI=allowed`.

## Connected systems

| Connector | Used for | Status |
|---|---|---|
| Web (Tavily, or the synthetic internet) | Discovery and research | In use |
| CGS library (`knowledge/`) | Service fit and outreach; a synthetic demo library until CGS shares its own | In use |
| Email (Gmail, or a mock mailbox) | Drafts, approve-and-send, reading client replies | In use |
| CRM (Insightly, or a mock CRM) | Duplicate check; a lead from Step 4, statuses that follow the stages, notes, emails logged by BCC, conversion to an opportunity at Qualification | In use (team test account) |
| Google Drive | Direction sources | Not built |

## Run it (Windows)

1. `Setup.cmd` once: creates `.venv` and installs `requirements.lock`.
2. Copy `.env.example` to `.env`. The defaults run the **demo** (`APP_MODE=demo`): seeded synthetic data, text rules
   instead of AI, no keys needed.
3. `Start Workspace v2.cmd`, then open http://127.0.0.1:8800/docs for the interactive API, or http://127.0.0.1:8800/
   for the workspace page (the UI is still being merged with the v2 backend).

For real AI, set `APP_MODE=live`, `AI_PROVIDER` (`gemini`, `openai` or `claude`) and its key, and `TAVILY_API_KEY` for
real web search. Email and Insightly credentials live outside the project folder, in `%USERPROFILE%\.cgs-workspace\`
(`gmail.env`, `insightly.env`); `.env.example` explains each setting.

To use Insightly: create the custom fields and statuses that `config/crm.json` names in your Insightly account, then
set `CRM_MODE=write_approved` and `CRM_PROVIDER=insightly`. `Check CRM.cmd` checks the account (reads only);
`Check CRM.cmd --write` runs one synthetic company through Steps 4–8 into Insightly.

## Checks

- Offline tests: `python -m pytest tests --ignore=tests/research/test_app_and_contract.py --ignore=tests/research/test_five_company_workflow.py`
  (about 500 tests, no network).
- Live checks with the real AI on the synthetic internet: `Check AI.cmd`, `Check Research.cmd`, `Check Workflow.cmd`,
  `Check Agents.cmd`, `Check Orchestrator.cmd`; `AI Exam.cmd` grades the evidence agent on cases with known answers.
- Connections: `Check Mail.cmd` (Gmail), `Check CRM.cmd` (Insightly).

## Layout

| Path | What it is |
|---|---|
| `api/` | The v2 backend (FastAPI): workflow services, agents (`*_agent.py`), autonomy and the job board, CRM sync |
| `api/toolkit/` | The connector gate, permissions and usage; `mail.py` (Gmail), `crm.py` (Insightly) |
| `backend/rules.py` | The qualification rules engine (BANTHE weights, conditions, readiness) |
| `config/` | Search settings, toolkit permissions, CRM mapping, the research problem brief and the scoring rubric |
| `knowledge/` | The CGS library the agents may cite |
| `frontend/` | The workspace page |
| `tests/v2/` | Tests for the v2 backend; `tests/p0`–`p5`, `tests/research` cover the earlier components |
| `lead_agent/`, `workspace.py`, `server.py`, `demo.py`, `dist/` | The earlier integrated app (research worker and qualification prototype), still used by the research pipeline |
| `design-explorations/` | Standalone HTML interaction prototypes with simulated data |

## Documentation

- [Backend v2 guide](docs/BACKEND_V2.md): decisions, agents, autonomy, manager, toolkit, email, CRM, endpoints.
- [Database](docs/DATABASE.md) and [API](docs/API.md).
- [Search integration](docs/SEARCH_INTEGRATION.md) and [Research v2](docs/RESEARCH_V2.md).
- Earlier phases (P0–P5) and their verification: [development plan](docs/DEVELOPMENT_PLAN.md) and `docs/VERIFICATION_*.md`.
  The repository's earlier front page and layout are on the `legacy-v1` branch.

Engineering documentation, comments and UI copy are in English. `docs/upstream/` keeps original source archives unchanged.
