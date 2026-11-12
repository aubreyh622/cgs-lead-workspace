# Running the workspace on Google Cloud

This page is for the person at CGS who hosts the workspace on Google Cloud Run. The workspace still runs on one
computer exactly as before (`Start Workspace v2.cmd`); nothing here is needed for that.

**Status.** The code changes below are tested on this team's computers: the full test suite passes on SQLite, and the
workspace tests pass on PostgreSQL 18. The container has **not** been built and the `gcloud` commands have **not**
been run, because the team has no Google Cloud project. Check each command against Google's current documentation the
first time you use it.

## What runs where

| Part | On one computer | On Google Cloud |
|---|---|---|
| Web server and agents | `python -m api.main` | the same command, in a container on Cloud Run |
| Database | a SQLite file in `runtime/` | Cloud SQL for PostgreSQL (`DATABASE_URL`) |
| Keys and passwords | `.env` and `%USERPROFILE%\.cgs-workspace\*.env` | Secret Manager, given to the container as environment variables |
| Sign-in | none (each person picks their name) | Google sign-in through Identity-Aware Proxy, checked by the workspace (`CGS_LOGIN=google`) |
| CGS library | the `knowledge/` folder, and Google Drive folders if set | Google Drive folders (`DRIVE_PROVIDER=google`, `DRIVE_AUTH=metadata`, folders shared with the service account), plus the `knowledge/` folder inside the image |

## Sign-in

On one computer each person picks their name. Hosted, that would let anyone who reaches the address read every lead
and approve emails under any name, so a hosted workspace uses Google sign-in:

1. Put Google's Identity-Aware Proxy (IAP) in front of the Cloud Run service and allow only the CGS accounts that
   should use the workspace. IAP makes each person sign in with Google and adds a signed note to every request.
2. Set `CGS_LOGIN=google`. The workspace then checks that note on every request (the page and reads included, the
   health check `/api/health` excepted): Google signed it, it was written for this workspace, it has not expired.
3. Set `CGS_IAP_AUDIENCE` to the audience shown on the IAP page for this service. For a Cloud Run service with IAP
   switched on directly it has the form `/projects/PROJECT_NUMBER/locations/REGION/services/SERVICE_NAME`; behind a
   load balancer it is `/projects/PROJECT_NUMBER/global/backendServices/SERVICE_ID`.
4. Give each person's Google email in `CGS_USERS`: `Alex Morgan <alex@cgs.example>, Sarah Patel <sarah@cgs.example>`.
   The name is what every change is recorded under. An account that is not listed is refused, even if IAP lets it in.

The page no longer asks who is using it, and the name the page sends is ignored: nobody can act under another name.
To add a person, add them to `CGS_USERS` and deploy again; they join the existing workspace.

Not tested against a real IAP: the team has no Google Cloud project. The checking code is tested with notes signed
the same way (ES256) by a key made in the test (`tests/v2/test_login.py`). Check the audience format against
Google's IAP documentation ("Securing your app with signed headers").

Keep `--no-allow-unauthenticated` as well, so that only IAP can call the service.

## Settings

Every setting in `.env.example` is an environment variable and works the same way in the container. These are the
ones that matter for hosting:

| Variable | Value | Why |
|---|---|---|
| `CGS_HOST` | `0.0.0.0` (the Dockerfile sets it) | listen for requests from outside the container |
| `PORT` | set by Cloud Run | the port to listen on; wins over `CGS_V2_PORT` |
| `CGS_PUBLIC_HOSTS` | the service's host name, for example `cgs-workspace-xyz.a.run.app`; several separated by commas | the workspace refuses requests addressed to any other name, and writes from any other website |
| `DATABASE_URL` | `postgresql://USER:PASSWORD@/DBNAME?host=/cloudsql/PROJECT:REGION:INSTANCE` | Cloud SQL through the socket Cloud Run provides. Keep it in Secret Manager: it holds the password |
| `CGS_LOGIN`, `CGS_IAP_AUDIENCE`, `CGS_USERS` | see Sign-in above | who may use the workspace, and under which name |
| `APP_MODE` | `live` | real AI; `demo` uses text rules and the synthetic sample |
| `AI_PROVIDER`, `AI_MODEL` | `gemini` and a Gemini model | CGS's default reasoning engine |
| `GEMINI_BACKEND` | `agent_platform` | Gemini (and the library's embeddings) through Agent Platform in CGS's own project, signed by the service account: no `GEMINI_API_KEY` at all. See "Gemini without an API key" below |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | the project id; `global` (or a region such as `us-central1`) | where those requests go. Without `GOOGLE_CLOUD_PROJECT` the service account's own project is used |
| `OTEL_TO_CLOUD` | `on` | the agents' traces to Cloud Trace and metrics to Cloud Monitoring, without message text. See "Traces and metrics" below |
| `TZ` | the office's time zone, for example `America/New_York` | the daily patrol starts at an hour of the local day |

Secrets (store each in Secret Manager and map it to the variable of the same name): `DATABASE_URL`,
`TAVILY_API_KEY` (and `GEMINI_API_KEY` only if you keep `GEMINI_BACKEND=ai_studio`), and when used `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GMAIL_REFRESH_TOKEN`, `INSIGHTLY_API_KEY`, `INSIGHTLY_API_URL`. A value in the environment always wins over the
credentials files, so no file is needed in the container.

The workspace will not start with `CGS_HOST=0.0.0.0` unless `CGS_PUBLIC_HOSTS` is set.

## Gemini without an API key (Agent Platform)

With `GEMINI_BACKEND=agent_platform` the workspace sends Gemini requests to Agent Platform (formerly Vertex AI) in CGS's
project instead of to AI Studio, signed with the service account the Cloud Run service runs as (`api/google_cloud.py`).
There is no Gemini key to store, rotate or leak, and requests and billing stay in CGS's Google Cloud project. The
request bodies are the same, so agents and prompts are unchanged. Library embeddings (`KNOWLEDGE_EMBEDDINGS=gemini`)
follow the same setting.

1. Enable the Agent Platform API: `gcloud services enable aiplatform.googleapis.com`
2. Give the service account the Agent Platform User role:
   `gcloud projects add-iam-policy-binding PROJECT --member=serviceAccount:SERVICE_ACCOUNT --role=roles/aiplatform.user`
3. Set `GEMINI_BACKEND=agent_platform`, `GOOGLE_CLOUD_LOCATION=global` (or a region the model is offered in) and
   `AI_MODEL` to a model id Agent Platform offers. `GOOGLE_CLOUD_PROJECT` is optional on Cloud Run.

On a laptop the same setting works after `gcloud auth application-default login`. To try Agent Platform without a
project, `GEMINI_BACKEND=express` uses an express-mode key from console.cloud.google.com/expressmode in
`GOOGLE_GENAI_API_KEY`: free for 90 days on a @gmail.com account, only some Gemini models, no embeddings (they stay on
AI Studio). `GET /api/config` shows which backend is in use (`gemini`). Like the rest of this page, it has been tested
only against fake responses, not against a real project.

## Traces and metrics (Cloud Trace, Cloud Monitoring)

With `OTEL_TO_CLOUD=on` the workspace sends what the agents' runtime (Google ADK) records to CGS's project
(`api/telemetry.py`): a trace for every agent run with each model request (model, tokens) and each tool call, and metrics
such as token use and tool duration per agent and model. The text of prompts, notes, emails and answers is **not**
sent; the workspace refuses to start the export if `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` asks for it,
unless `OTEL_CONTENT_TO_CLOUD=allowed`. The agent steps people see on the Agents page are still kept in the database.

1. Enable the APIs: `gcloud services enable telemetry.googleapis.com cloudtrace.googleapis.com monitoring.googleapis.com`
2. Give the service account `roles/cloudtrace.agent` and `roles/monitoring.metricWriter`.
3. Set `OTEL_TO_CLOUD=on`. The packages are in `requirements-cloud.txt`, which the Dockerfile installs.

The start-up log says whether it started (`Cloud traces and metrics: on ...`) or why not; `GET /api/config` shows the
same (`cloudTelemetry`). A problem never stops the workspace.

## One instance, always running

The agents and the email clock are threads inside the server process, and the job board assumes one server per
database. Two rules follow:

- **Exactly one instance**: `--min-instances 1 --max-instances 1`. A second instance on the same database would take
  over the first one's jobs.
- **CPU always allocated**: `--no-cpu-throttling`. By default Cloud Run pauses the CPU between requests, which would
  stop the agents and delay approved emails until someone opens the page.

When the instance restarts, work that stopped in the middle goes back on the board, and an email that was being sent
is marked "check Gmail's Sent folder" instead of being sent twice (`api/autonomy.py`, `recover_after_restart`).

## Files do not survive a restart

A Cloud Run container's disk is temporary. Everything that must last is in the database. What is lost on a restart:

- `runtime/embeddings-*.json`, the cache for the library's meaning search. It is rebuilt on first use.
- `runtime/research.sqlite3`, used only with `RESEARCH_ENGINE=pipeline`. Keep the default, `RESEARCH_ENGINE=agent`.

The `knowledge/` folder is copied into the image when it is built, so library documents must be in the folder at
build time. `knowledge/cgs-public/` is not in the Git repository; run `python -m api.fetch_cgs_site` before building
if you want those pages. Documents copied from Google Drive (`DRIVE_PROVIDER=google`) are in the database
(`drive_documents`), so they survive a restart and need no rebuild: set `DRIVE_AUTH=metadata`, enable the Google Drive
API in the project, and share the library folders with the service account the service runs as.

## Deploy

Not run by the team; shown as a starting point.

```bash
gcloud run deploy cgs-workspace \
  --source . \
  --region REGION \
  --no-allow-unauthenticated \
  --min-instances 1 --max-instances 1 --no-cpu-throttling \
  --memory 1Gi \
  --add-cloudsql-instances PROJECT:REGION:INSTANCE \
  --env-vars-file cloud-env.yaml \
  --set-secrets DATABASE_URL=cgs-database-url:latest,TAVILY_API_KEY=tavily-api-key:latest
```

`cloud-env.yaml` holds the settings that are not secrets (the list of people contains commas, which a file handles
and `--set-env-vars` does not):

```yaml
APP_MODE: live
AI_PROVIDER: gemini
GEMINI_BACKEND: agent_platform
GOOGLE_CLOUD_LOCATION: global
OTEL_TO_CLOUD: "on"
TZ: America/New_York
CGS_PUBLIC_HOSTS: SERVICE_HOST
CGS_LOGIN: google
CGS_IAP_AUDIENCE: AUDIENCE
CGS_USERS: "Name One <one@cgs.example>, Name Two <two@cgs.example>"
```

The service's host name is known only after the first deploy. Deploy once, read the address, then set
`CGS_PUBLIC_HOSTS` to it and deploy again; until then every request is refused with `bad_host`.

The tables are created on first start. A newer version adds its new columns to an existing database by itself
(`api/db.py`, `add_missing_columns`).

## Gmail on a hosted workspace

`python -m api.toolkit.gmail_auth` opens a browser, so run it on a computer, then store the three Gmail values it
writes to `gmail.env` as secrets. Use an OAuth client that belongs to CGS's Google Workspace with user type
**Internal**: a client left in "Testing" loses its sign-in after seven days.

## Checking PostgreSQL yourself

```bash
pip install -r requirements-cloud.txt
set CGS_TEST_POSTGRES=postgresql://postgres@127.0.0.1:5432
pytest tests/v2 --deselect tests/v2/test_db_migration.py --deselect tests/v2/test_database.py --deselect tests/v2/test_research_live.py::test_old_database_gets_new_columns
```

Each test gets its own database on that server (`tests/conftest.py`), so the run takes several times longer than on
SQLite. The deselected tests read the SQLite file itself.
