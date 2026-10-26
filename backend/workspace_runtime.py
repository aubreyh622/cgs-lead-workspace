"""Load both existing modules while preserving their separate SQLite schemas."""
from __future__ import annotations

from dataclasses import dataclass
from http.server import ThreadingHTTPServer
import secrets

from server import Store as QualificationStore
from .research_http import WorkspaceHandler
from .research_service import ResearchJobStore, ResearchService
from .opportunity_service import OpportunityService
from .handoff_service import HandoffService
from .theme_store import ThemeStore
from .orchestrator_service import OrchestratorService
from .workspace_config import WorkspaceConfig


@dataclass
class WorkspaceStores:
    research: ResearchJobStore
    qualification: QualificationStore

    @classmethod
    def open(cls, config: WorkspaceConfig):
        config.validate()  # Check before either store can initialize or seed a database.
        qualification = QualificationStore(config.qualification_db)
        try:
            research = ResearchJobStore(config.research_db)
        except Exception:
            qualification.db.close()
            raise
        return cls(research, qualification)

    def close(self):
        # ResearchStore opens/closes connections per operation.
        self.qualification.db.close()


def make_workspace_server(config: WorkspaceConfig, stores: WorkspaceStores, *, research_provider_factory=None, manager_provider_factory=None, demo_companies=None):
    if demo_companies is not None and (research_provider_factory is None or manager_provider_factory is None):
        raise ValueError("Offline demo mode requires explicit synthetic provider factories.")
    server = ThreadingHTTPServer(("127.0.0.1", config.port), WorkspaceHandler)
    server.daemon_threads = True
    server.store = stores.qualification
    server.research_store = stores.research
    server.csrf = secrets.token_urlsafe(32)
    server.workspace_config = config
    server.offline_demo = demo_companies is not None
    server.research_service = ResearchService(config.root, stores.research,
        config.providers["research"]["model"], research_provider_factory)
    server.research_service.demo_companies = demo_companies
    server.opportunity_service = OpportunityService(server.research_service)
    server.handoff_service = HandoffService(server.opportunity_service, stores.qualification)
    server.theme_store = ThemeStore(config.root, stores.research)
    server.research_service.theme_store = server.theme_store
    server.orchestrator_service = OrchestratorService(server.research_service, server.opportunity_service,
        server.handoff_service, server.theme_store, config.providers["research"]["model"], manager_provider_factory)
    return server
