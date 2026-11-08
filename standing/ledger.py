"""Transactional metadata only. Each operation owns and closes its connection."""
from contextlib import contextmanager
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Ledger:
    def __init__(self, connect, default_level=1, promote_after=10, max_auto_level=1):
        self.connect = connect
        self.default_level = default_level
        self.promote_after = promote_after
        self.max_auto_level = max_auto_level
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS actors (name TEXT PRIMARY KEY, level INTEGER NOT NULL, '
                       'passes_window INTEGER NOT NULL, fails_total INTEGER NOT NULL, updated_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, '
                       'actor TEXT NOT NULL, task_id TEXT, kind TEXT NOT NULL, detail_hash TEXT NOT NULL, '
                       'result TEXT NOT NULL, reason TEXT NOT NULL)')

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.row_factory = __import__('sqlite3').Row
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _actor(self, db, name, initial=None):
        db.execute('INSERT OR IGNORE INTO actors VALUES (?, ?, 0, 0, ?)',
                   (name, self.default_level if initial is None else initial, now()))
        return dict(db.execute('SELECT * FROM actors WHERE name=?', (name,)).fetchone())

    @staticmethod
    def _event(db, name, task, kind, detail_hash, result, reason):
        return db.execute('INSERT INTO events(at,actor,task_id,kind,detail_hash,result,reason) '
                          'VALUES (?,?,?,?,?,?,?)',
                          (now(), name, task or None, kind, detail_hash, result, reason)).lastrowid

    def seed(self, levels):
        # Initial levels only: reloading must never undo a demotion or an owner change.
        with self.transaction() as db:
            for name, level in levels.items():
                self._actor(db, name, level)

    def actor(self, name):
        with self.transaction() as db:
            return self._actor(db, name)

    def proof(self, name, task, kind, detail_hash, passed, reason):
        with self.transaction() as db:
            actor = self._actor(db, name)
            level, passes, fails = actor['level'], actor['passes_window'], actor['fails_total']
            if passed is True:
                passes += 1
                if passes >= self.promote_after and level < self.max_auto_level:
                    level += 1
                    passes = 0
            elif passed is False:
                level = max(0, level - 1)
                passes = 0  # A failure starts a new clean promotion window, even at L0.
                fails += 1
            if passed is not None:
                db.execute('UPDATE actors SET level=?, passes_window=?, fails_total=?, updated_at=? WHERE name=?',
                           (level, passes, fails, now(), name))
            result = 'unavailable' if passed is None else 'pass' if passed else 'fail'
            event_id = self._event(db, name, task, kind, detail_hash, result, reason)
            if level != actor['level']:
                self._event(db, name, task, 'level', '', f'L{level}',
                            'Passing checks earned a higher level.' if passed else 'A failed check lowered the level.')
            state = dict(db.execute('SELECT * FROM actors WHERE name=?', (name,)).fetchone())
            # Captured inside the same transaction, not a racy pre-proof actor read.
            state['previous_level'] = actor['level']
            return event_id, state

    def set_level(self, name, level, reason_hash=''):
        with self.transaction() as db:
            self._actor(db, name)
            db.execute('UPDATE actors SET level=?, passes_window=0, updated_at=? WHERE name=?', (level, now(), name))
            self._event(db, name, None, 'owner', reason_hash, f'L{level}', 'Owner set the level.')

    def listing(self):
        with self.transaction() as db:
            return [dict(row) for row in db.execute('SELECT actors.*, '
                    '(SELECT result || ": " || reason FROM events WHERE actor=actors.name ORDER BY id DESC LIMIT 1) '
                    'AS last_event FROM actors ORDER BY name')]

    def log(self, name):
        with self.transaction() as db:
            return [dict(row) for row in db.execute('SELECT * FROM events WHERE actor=? ORDER BY id DESC LIMIT 20', (name,))]
