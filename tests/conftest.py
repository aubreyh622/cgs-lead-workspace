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
