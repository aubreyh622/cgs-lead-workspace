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
| Sign-in | none (each person picks their name) | Google sign-in in front of the service (see below) |
| CGS library | the `knowledge/` folder | the `knowledge/` folder inside the image (a Google Drive connector is planned) |

## Before you deploy: sign-in

The workspace has no login of its own. Anyone who can reach its address can read every lead and approve emails.
On Cloud Run, deploy it with `--no-allow-unauthenticated` and put Google sign-in in front of it (Identity-Aware Proxy),
limited to CGS accounts. Do not make the service public.

## Settings

Every setting in `.env.example` is an environment variable and works the same way in the container. These are the
ones that matter for hosting:

| Variable | Value | Why |
|---|---|---|
| `CGS_HOST` | `0.0.0.0` (the Dockerfile sets it) | listen for requests from outside the container |
| `PORT` | set by Cloud Run | the port to listen on; wins over `CGS_V2_PORT` |
| `CGS_PUBLIC_HOSTS` | the service's host name, for example `cgs-workspace-xyz.a.run.app`; several separated by commas | the workspace refuses requests addressed to any other name, and writes from any other website |
| `DATABASE_URL` | `postgresql://USER:PASSWORD@/DBNAME?host=/cloudsql/PROJECT:REGION:INSTANCE` | Cloud SQL through the socket Cloud Run provides. Keep it in Secret Manager: it holds the password |
| `APP_MODE` | `live` | real AI; `demo` uses text rules and the synthetic sample |
| `AI_PROVIDER`, `AI_MODEL` | `gemini` and a Gemini model | CGS's default reasoning engine |
| `TZ` | the office's time zone, for example `America/New_York` | the daily patrol starts at an hour of the local day |

Secrets (store each in Secret Manager and map it to the variable of the same name): `DATABASE_URL`,
`GEMINI_API_KEY`, `TAVILY_API_KEY`, and when used `ANTHROPIC_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GMAIL_REFRESH_TOKEN`, `INSIGHTLY_API_KEY`, `INSIGHTLY_API_URL`. A value in the environment always wins over the
credentials files, so no file is needed in the container.

The workspace will not start with `CGS_HOST=0.0.0.0` unless `CGS_PUBLIC_HOSTS` is set.

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
if you want those pages.

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
  --set-env-vars APP_MODE=live,AI_PROVIDER=gemini,CGS_PUBLIC_HOSTS=SERVICE_HOST,TZ=America/New_York,CGS_USERS="Name One,Name Two" \
  --set-secrets DATABASE_URL=cgs-database-url:latest,GEMINI_API_KEY=gemini-api-key:latest,TAVILY_API_KEY=tavily-api-key:latest
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
