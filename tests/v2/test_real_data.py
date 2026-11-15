"""Real public company data (api/real_data.py): its own database, the real web, and nothing that can reach a real person."""
import os

from api import real_data as RD


def test_real_data_runs_live_on_the_real_web_with_email_and_crm_off(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_MODE", "mock")  # recorded, so the real-web setting below is undone after the test
    s = RD.real_settings(tmp_path / "real.sqlite3")
    assert os.environ["WEB_MODE"] == "google"  # the synthetic web is never the real web; Google Search is the default
    assert (s.app_mode, s.mail_provider, s.crm_mode, s.research_engine) == ("live", "off", "disabled", "agent")
    assert s.agent_worker is True  # research for several companies runs in parallel threads
    assert tmp_path.as_posix() in s.database_url  # never the synthetic demo database
    assert RD.DEFAULT_DB != "runtime/workspace-v2.sqlite3"
    monkeypatch.setenv("WEB_MODE", "tavily")
    RD.real_settings(tmp_path / "real.sqlite3")
    assert os.environ["WEB_MODE"] == "tavily"  # Tavily stays available by choice


def test_real_data_needs_a_search_key(monkeypatch):
    monkeypatch.delenv("WEB_MODE", raising=False)
    monkeypatch.setenv("GEMINI_BACKEND", "ai_studio")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    assert RD.main(["--pick", "0"]) == 1
    monkeypatch.setenv("WEB_MODE", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    assert RD.main(["--pick", "0"]) == 1
