# Reused patterns and reference versions

References inspected on 20 October 2026. This is a custom CGS component, not a bundled
prospecting suite. No new paid provider SDK or service was added from these repositories.

- **Fire Enrich** (`firecrawl/fire-enrich`), revision
  `94e061dc5bab94e5838cceb8b3d5e6af329d2d50`, MIT. Adapted its narrow concept of nullable
  typed fields with per-source exact evidence and separate confidence into Pydantic
  schemas. Files inspected: `lib/services/openai.ts` and
  `lib/agent-architecture/core/types.ts`. Intake is company/domain, not email; evidence
  is checked against stored source text. Its OpenAI, Firecrawl, email parsing, multi-agent
  architecture and fallback behavior are not included. License: `third_party/Fire_Enrich_LICENSE.txt`.
  [Pinned source](https://github.com/firecrawl/fire-enrich/tree/94e061dc5bab94e5838cceb8b3d5e6af329d2d50).
- **Tavily Company Researcher** (`tavily-ai/tavily_company_researcher`), revision
  `d78b5fd3d775401f597be4f0a1e37b297aab5c9c`, MIT. Architecture reference for grounding a
  company on its official site and disambiguating before writing a report. Its multiple
  agents are replaced by one model and ordinary validation/worker code. License preserved
  in `third_party/Tavily_Company_Researcher_LICENSE.txt`.
  [Pinned source](https://github.com/tavily-ai/tavily_company_researcher/tree/d78b5fd3d775401f597be4f0a1e37b297aab5c9c).
- **OpenOutbound** (`xyluxx/openoutbound`), inspected tree
  `bc158cac6548ab54699442d159cc2130570b9a78`. `src/modules/research/validate.ts` and
  `pipeline.ts` were read as references for source membership, partial results and
  retaining paid research across retries. No code is copied. The CGS validator adds exact
  excerpt, date, entity and human claim-support checks. A tree hash identifies the
  inspected file state; it is not presented as a release or maintenance endorsement.
- **Earlier local CGS prototype**: retained the Python/Streamlit/Gemini/Tavily stack and
  provider-error redaction approach. The application here is separate, with a new record
  schema and storage. The old application attributed its research-loop pattern to
  LangChain data-enrichment revision `3af674226ffdb23dbd20ae933936c19d8b99e72c` (MIT).
  That notice is retained in `third_party/LangChain_LICENSE.txt`; LangGraph is not required
  by this implementation.
- **SDRbot** remains a deferred contact-adapter reference. No contact provider is installed.

API access and data costs are separate from repository licenses. Source inspection,
offline provider tests and a synthetic workflow demo do not establish live provider
availability or business lead quality. Pinned Python dependencies are in `requirements.lock`.
