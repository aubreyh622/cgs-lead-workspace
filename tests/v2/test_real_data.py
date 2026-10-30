"""Real public company data (api/real_data.py): its own database, the real web, and nothing that can reach a real person."""
import os

from api import real_data as RD


def test_real_data_runs_live_on_the_real_web_with_email_and_crm_off(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_MODE", "mock")  # recorded, so the tavily setting below is undone after the test
    s = RD.real_settings(tmp_path / "real.sqlite3")
    assert os.environ["WEB_MODE"] == "tavily"
    assert (s.app_mode, s.mail_provider, s.crm_mode, s.research_engine) == ("live", "off", "disabled", "agent")
    assert s.agent_worker is True  # research for several companies runs in parallel threads
    assert tmp_path.as_posix() in s.database_url  # never the synthetic demo database
    assert RD.DEFAULT_DB != "runtime/workspace-v2.sqlite3"


def test_real_data_needs_a_search_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert RD.main(["--pick", "0"]) == 1
