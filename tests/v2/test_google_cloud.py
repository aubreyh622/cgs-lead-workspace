"""Gemini on Google Cloud's Agent Platform (api/google_cloud.py): the same request body as AI Studio, sent to CGS's project and
signed by the service account; express mode with its own key; embeddings by :predict. Offline: fake transports and token."""
import copy

import pytest

from api import agent_runtime as R
from api import embeddings as EMB
from api import google_cloud as GC
from api.llm import LLMError, make_provider


class Wire:
    """A fake AI company that keeps url, headers and body of every request."""

    def __init__(self, *responses):
        self.responses, self.seen = list(responses), []

    def __call__(self, url, headers, payload, **kw):
        self.seen.append((url, dict(headers), copy.deepcopy(payload)))
        return copy.deepcopy(self.responses.pop(0))


def answer(*parts):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}}], "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1}}


ECHO = R.Tool("echo", "Repeat a word.", R.params({"word": {"type": "string"}}), lambda a: {"echo": a["word"]})
SPEC = lambda: R.AgentSpec(name="Test agent", version="t1", system="Test.", tools=[ECHO],
                           finish=R.Tool("finish", "Answer.", R.params({"answer": {"type": "string"}}), lambda a: a))
SCRIPT = lambda: (answer({"functionCall": {"name": "echo", "args": {"word": "hi"}, "id": "c1"}, "thoughtSignature": "sig"}),
                  answer({"functionCall": {"name": "finish", "args": {"answer": "ok"}}}))


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setattr(GC, "_token_source", lambda: "token-123")
    monkeypatch.setenv("GEMINI_BACKEND", "agent_platform")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "cgs-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")


@pytest.mark.parametrize("runtime", ["legacy", "adk"])
def test_agent_platform_sends_the_same_body_to_cgs_project_with_a_token(cloud, monkeypatch, runtime):
    if runtime == "adk":
        pytest.importorskip("google.adk")
    monkeypatch.setenv("AGENT_RUNTIME", runtime)
    # the request to AI Studio, for comparison
    monkeypatch.setenv("GEMINI_BACKEND", "ai_studio")
    studio = Wire(*SCRIPT())
    R.run(SPEC(), make_provider("gemini", "gemini-x", key="k", transport=studio), "Say hi.")
    monkeypatch.setenv("GEMINI_BACKEND", "agent_platform")
    on_cloud = Wire(*SCRIPT())
    assert R.run(SPEC(), make_provider("gemini", "gemini-x", transport=on_cloud), "Say hi.").status == "done"  # no key needed
    url, headers, _ = on_cloud.seen[0]
    assert url == ("https://us-central1-aiplatform.googleapis.com/v1/projects/cgs-project/locations/us-central1"
                   "/publishers/google/models/gemini-x:generateContent")
    assert headers == {"Authorization": "Bearer token-123"}
    assert [b for _, _, b in on_cloud.seen] == [b for _, _, b in studio.seen]  # thought signature and all
    assert studio.seen[0][0].startswith("https://generativelanguage.googleapis.com/") and studio.seen[0][1] == {"x-goog-api-key": "k"}


def test_global_location_and_project_from_the_credentials(monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "agent_platform")
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    def token():
        GC.CREDENTIALS.project = "from-adc"
        return "t"
    monkeypatch.setattr(GC, "_token_source", token)
    monkeypatch.setattr(GC.CREDENTIALS, "project", None)
    assert GC.model_url("m", "generateContent") == \
        "https://aiplatform.googleapis.com/v1/projects/from-adc/locations/global/publishers/google/models/m:generateContent"
    assert GC.describe() == "Agent Platform (project from the credentials, global)"


def test_express_mode_uses_its_own_key(monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "express")
    monkeypatch.delenv("GOOGLE_GENAI_API_KEY", raising=False)
    with pytest.raises(LLMError) as e:
        make_provider("gemini", "gemini-x")
    assert e.value.kind == "auth" and "GOOGLE_GENAI_API_KEY" in str(e.value)
    monkeypatch.setenv("GOOGLE_GENAI_API_KEY", "express-key")
    wire = Wire(answer({"text": '{"label": "meet"}'}))
    data, _ = make_provider("gemini", "gemini-x", transport=wire).json("s", "u", {"type": "object"}, "x")
    assert data == {"label": "meet"}
    assert wire.seen[0][0] == "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-x:generateContent"
    assert wire.seen[0][1] == {"x-goog-api-key": "express-key"}


def test_wrong_setting_and_missing_credentials_say_what_to_do(cloud, monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "vertex")
    with pytest.raises(LLMError) as e:
        make_provider("gemini", "m")
    assert e.value.kind == "config" and "GEMINI_BACKEND" in str(e.value)
    monkeypatch.setenv("GEMINI_BACKEND", "agent_platform")

    def no_creds():
        raise GC.CloudConfigError("found no Google Cloud credentials")
    monkeypatch.setattr(GC, "_token_source", no_creds)
    with pytest.raises(LLMError) as e:
        make_provider("gemini", "m", transport=Wire()).json("s", "u", {"type": "object"}, "x")
    assert e.value.kind == "auth" and "credentials" in str(e.value)


def test_claude_and_openai_keep_their_keys(cloud, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError):
        make_provider("claude", "m")  # GEMINI_BACKEND changes Gemini only


def test_embeddings_on_agent_platform_without_a_key(cloud, monkeypatch, tmp_path):
    monkeypatch.setenv("KNOWLEDGE_EMBEDDINGS", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    e = EMB.from_env()
    assert e is not None and e.backend == "agent_platform" and e.key == ""
    wire = Wire({"predictions": [{"embeddings": {"values": [0.1, 0.2]}}]}, {"predictions": [{"embeddings": {"values": [0.3, 0.4]}}]})
    e = EMB.Embedder("gemini", "", transport=wire, cache_dir=tmp_path, backend="agent_platform")
    assert e.embed(["first passage", "second passage"]) == [[0.1, 0.2], [0.3, 0.4]]
    url, headers, body = wire.seen[0]
    assert url.endswith("/projects/cgs-project/locations/us-central1/publishers/google/models/gemini-embedding-001:predict")
    assert headers == {"Authorization": "Bearer token-123"}
    assert body == {"instances": [{"content": "first passage", "task_type": "RETRIEVAL_DOCUMENT"}], "parameters": {"outputDimensionality": 768}}
    bad = EMB.Embedder("gemini", "", transport=Wire({"error": "x"}), cache_dir=tmp_path / "b", backend="agent_platform")
    with pytest.raises(EMB.EmbeddingError):
        bad.embed(["third passage"])


def test_express_keeps_embeddings_on_ai_studio(monkeypatch):
    monkeypatch.setenv("GEMINI_BACKEND", "express")
    monkeypatch.setenv("KNOWLEDGE_EMBEDDINGS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "studio-key")
    e = EMB.from_env()
    assert e.backend == "ai_studio" and e.key == "studio-key"
