"""Keep all pytest runs offline, including their Python subprocesses."""
import os
import sys
from pathlib import Path

GUARD_DIR = str(Path(__file__).resolve().parent / 'offline_guard')
sys.path.insert(0, GUARD_DIR)
from offline_guard import install
install()
existing = os.environ.get('PYTHONPATH', '')
os.environ['PYTHONPATH'] = GUARD_DIR + (os.pathsep + existing if existing else '')
# the CGS library on this machine (knowledge/, incl. the fetched public pages) must not change test results
os.environ['KNOWLEDGE_DIR'] = str(Path(__file__).resolve().parent / '_no_knowledge_here')
os.environ['KNOWLEDGE_EMBEDDINGS'] = 'off'  # meaning search needs a network service; tests use a fake one


def _use_postgres(server: str) -> None:
    """Run the same tests on PostgreSQL: CGS_TEST_POSTGRES=postgresql://user@127.0.0.1:5432 (a server, no database name).
    Each SQLite file a test asks for becomes its own database on that server, so tests stay separate and a test that
    opens the same file twice gets the same database twice; a database left by an earlier run is replaced. Leave out the
    tests that read the SQLite file itself (docs/DEPLOY_GOOGLE_CLOUD.md gives the command)."""
    import hashlib
    import psycopg
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    import api.db

    server = server.rstrip('/')
    made = set()

    def make_engine(db_url: str):
        name = 't_' + hashlib.md5(db_url.encode()).hexdigest()[:20]
        if name not in made:
            with psycopg.connect(server + '/postgres', autocommit=True) as conn:
                conn.execute(f'drop database if exists {name} with (force)')  # left by an earlier run of the same test
                conn.execute(f'create database {name}')
            made.add(name)
        # NullPool: hundreds of test databases must not each keep connections open
        return create_engine('postgresql+psycopg://' + server.split('://', 1)[1] + '/' + name, poolclass=NullPool)

    api.db.make_engine = make_engine


if os.environ.get('CGS_TEST_POSTGRES'):
    _use_postgres(os.environ['CGS_TEST_POSTGRES'])
