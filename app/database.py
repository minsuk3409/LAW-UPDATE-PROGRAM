import json
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

DEFAULTS = ['산업안전보건법', '산업안전보건법 시행령', '산업안전보건법 시행규칙', '산업안전보건기준에 관한 규칙']


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def default_path():
    return os.environ.get('LAW_WEB_DB', str(Path(__file__).resolve().parent.parent / '.data/laws.sqlite3'))


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS laws(
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, official_id TEXT,
            active INTEGER NOT NULL DEFAULT 1, last_success TEXT);
        CREATE TABLE IF NOT EXISTS versions(
            id INTEGER PRIMARY KEY, law_id INTEGER NOT NULL REFERENCES laws(id),
            version_key TEXT NOT NULL, mst TEXT NOT NULL, source TEXT NOT NULL,
            category TEXT NOT NULL, promulgated TEXT NOT NULL, effective TEXT NOT NULL,
            revision TEXT NOT NULL, number TEXT NOT NULL, official_url TEXT NOT NULL,
            digest TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            UNIQUE(law_id, version_key));
        CREATE TABLE IF NOT EXISTS bodies(
            id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES versions(id),
            digest TEXT NOT NULL, collected_at TEXT NOT NULL,
            articles TEXT NOT NULL, supplementary TEXT NOT NULL, raw TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS changes(
            id INTEGER PRIMARY KEY, law_id INTEGER NOT NULL REFERENCES laws(id),
            version_id INTEGER NOT NULL REFERENCES versions(id),
            kind TEXT NOT NULL, detected_at TEXT NOT NULL,
            before_body_id INTEGER REFERENCES bodies(id), after_body_id INTEGER NOT NULL REFERENCES bodies(id),
            review_status TEXT NOT NULL DEFAULT '미확인', memo TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS jobs(
            id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
            law_id INTEGER NOT NULL REFERENCES laws(id), checked_at TEXT NOT NULL,
            status TEXT NOT NULL, message TEXT NOT NULL);
        ''')
        if db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone() is None:
            db.executemany('INSERT OR IGNORE INTO laws(name) VALUES(?)', [(n,) for n in DEFAULTS])
            db.execute("INSERT INTO settings VALUES('initialized','1')")


def rows(db, sql, params=()):
    return [dict(r) for r in db.execute(sql, params)]


def latest_body(db, version_id):
    row = db.execute('SELECT * FROM bodies WHERE version_id=? ORDER BY collected_at DESC, id DESC LIMIT 1', (version_id,)).fetchone()
    if not row:
        return None
    body = dict(row)
    body['articles'] = json.loads(body['articles'])
    body['supplementary'] = json.loads(body['supplementary'])
    body.pop('raw')
    return body
