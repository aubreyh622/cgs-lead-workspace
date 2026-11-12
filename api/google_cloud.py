"""Where Gemini requests go, and how they are signed: Google AI Studio with an API key (the default), or Google Cloud's Agent
Platform (formerly Vertex AI) in CGS's own project, signed by the service account the workspace runs as. No API key then.

GEMINI_BACKEND (in .env or the Cloud Run settings):
  ai_studio       generativelanguage.googleapis.com with GEMINI_API_KEY (the default)
  agent_platform  {location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/...
                  Signed with Application Default Credentials: on Cloud Run the service account the service runs as (give it
                  the "Agent Platform User" role, roles/aiplatform.user); on a laptop, `gcloud auth application-default login`.
                  GOOGLE_CLOUD_PROJECT (default: the credentials' own project), GOOGLE_CLOUD_LOCATION (default: global).
  express         Agent Platform express mode, for trying it out: aiplatform.googleapis.com/v1/publishers/... with the express
                  key in GOOGLE_GENAI_API_KEY. Free for 90 days on a @gmail.com account, Gemini generateContent only.

The request bodies are the same on every backend (Agent Platform accepts parametersJsonSchema and responseJsonSchema), so
agents and prompts do not change. Embeddings differ: see api/embeddings.py.
"""
from __future__ import annotations

import os
import threading

BACKENDS = ("ai_studio", "agent_platform", "express")
SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class CloudConfigError(Exception):
    """A setting is missing or wrong; the message says which."""


def gemini_backend() -> str:
    name = os.environ.get("GEMINI_BACKEND", "ai_studio").strip().lower() or "ai_studio"
    if name not in BACKENDS:
        raise CloudConfigError(f"GEMINI_BACKEND must be one of {BACKENDS}, not {name!r}.")
    return name


def express_key() -> str:
    return os.environ.get("GOOGLE_GENAI_API_KEY", "").strip()


class _Credentials:
    """Application Default Credentials, refreshed shortly before they expire. One per process, shared by all threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._creds = None
        self.project: str | None = None

    def _load(self):
        try:
            import google.auth
            from google.auth.exceptions import DefaultCredentialsError
        except ImportError:  # pragma: no cover - google-auth comes with google-adk (requirements.lock)
            raise CloudConfigError("GEMINI_BACKEND=agent_platform needs google-auth: pip install -r requirements.lock") from None
        try:
            self._creds, self.project = google.auth.default(scopes=[SCOPE])
        except DefaultCredentialsError:
            raise CloudConfigError("GEMINI_BACKEND=agent_platform found no Google Cloud credentials. On Cloud Run the service "
                                   "account provides them; on a laptop run: gcloud auth application-default login") from None

    def token(self) -> str:
        with self._lock:
            if self._creds is None:
                self._load()
            if not self._creds.valid:
                from google.auth.transport.requests import Request
                from google.auth.exceptions import RefreshError, TransportError
                try:
                    self._creds.refresh(Request())
                except (RefreshError, TransportError) as e:
                    raise CloudConfigError(f"Could not get a Google Cloud access token ({type(e).__name__}). "
                                           "Check the service account, or run: gcloud auth application-default login") from None
            return self._creds.token


CREDENTIALS = _Credentials()
_token_source = CREDENTIALS.token  # tests replace this


def project() -> str:
    p = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if p:
        return p
    if CREDENTIALS.project is None:
        _token_source()  # loads the credentials, which may name their project
    if not CREDENTIALS.project:
        raise CloudConfigError("Set GOOGLE_CLOUD_PROJECT to the Google Cloud project that runs Gemini.")
    return CREDENTIALS.project


def location() -> str:
    return os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip() or "global"


def model_url(model: str, method: str, backend: str | None = None) -> str:
    """The URL of one model method (generateContent, predict ...) on the chosen backend."""
    backend = backend or gemini_backend()
    if backend == "ai_studio":
        return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}"
    if backend == "express":
        return f"https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:{method}"
    loc = location()
    host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{project()}/locations/{loc}/publishers/google/models/{model}:{method}"


def auth_headers(api_key: str = "", backend: str | None = None) -> dict:
    """The headers that sign one request: an API key (AI Studio, express) or a bearer token (Agent Platform)."""
    backend = backend or gemini_backend()
    if backend == "agent_platform":
        return {"Authorization": f"Bearer {_token_source()}"}
    return {"x-goog-api-key": api_key}


def describe() -> str:
    """One line for the start-up message and /api/config."""
    try:
        b = gemini_backend()
    except CloudConfigError as e:
        return str(e)
    if b == "agent_platform":
        return f"Agent Platform ({os.environ.get('GOOGLE_CLOUD_PROJECT', '').strip() or 'project from the credentials'}, {location()})"
    return {"ai_studio": "AI Studio (API key)", "express": "Agent Platform express mode (API key)"}[b]
