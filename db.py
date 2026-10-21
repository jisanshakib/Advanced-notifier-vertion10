"""Storage. Uses PostgreSQL when DATABASE_URL is set (needed on cloud hosts, whose
local disk is wiped on every restart). Otherwise falls back to a local SQLite file."""
import os
import sqlite3
import threading

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
_lock = threading.Lock()

if PG:
    import psycopg
    from psycopg.rows import dict_row

    IntegrityError = psycopg.IntegrityError

    def _connect():
        # prepare_threshold=None: Supabase/PgBouncer transaction pooler (port 6543) breaks
        #   server-side prepared statements ("prepared statement ... already exists").
        # keepalives: detect connections silently dropped by NAT/poolers after long idle.
        return psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row,
                               prepare_threshold=None, connect_timeout=10,
                               keepalives=1, keepalives_idle=30, keepalives_interval=10,
                               keepalives_count=3)

    _conn = _connect()
    _SCHEMA = [
        "CREATE TABLE IF NOT EXISTS channels (id BIGINT PRIMARY KEY, title TEXT, username TEXT, priority INTEGER NOT NULL DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS keywords (id SERIAL PRIMARY KEY, word TEXT NOT NULL)",
        "CREATE UNIQUE INDEX IF NOT EXISTS keywords_word_ci ON keywords (lower(word))",
        "CREATE TABLE IF NOT EXISTS priority_keywords (id SERIAL PRIMARY KEY, word TEXT NOT NULL)",
        "CREATE UNIQUE INDEX IF NOT EXISTS priority_keywords_word_ci ON priority_keywords (lower(word))",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS keyword_hits (id SERIAL PRIMARY KEY, channel_id BIGINT, channel_title TEXT, "
        "word TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'text', "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    ]
    for stmt in _SCHEMA:
        _conn.execute(stmt)
else:
    IntegrityError = sqlite3.IntegrityError
    _conn = sqlite3.connect(os.getenv("SQLITE_PATH", "botdata.sqlite3"), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS channels (id INTEGER PRIMARY KEY, title TEXT, username TEXT, priority INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS keywords (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT NOT NULL UNIQUE COLLATE NOCASE);
        CREATE TABLE IF NOT EXISTS priority_keywords (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT NOT NULL UNIQUE COLLATE NOCASE);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS keyword_hits (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER, channel_title TEXT,
            word TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'text',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);
        """
    )
    _conn.commit()


def q(sql, args=(), one=False):
    if PG:
        sql = sql.replace("?", "%s")
    global _conn
    with _lock:
        if PG:
            try:
                if _conn.closed:
                    raise psycopg.OperationalError("connection closed")
                cur = _conn.execute(sql, args)
                rows = cur.fetchall() if cur.description else []
            except (psycopg.OperationalError, psycopg.InterfaceError):
                # idle connection dropped by the server/pooler -> reconnect once and retry
                try:
                    _conn.close()
                except Exception:
                    pass
                _conn = _connect()
                cur = _conn.execute(sql, args)
                rows = cur.fetchall() if cur.description else []
        else:
            try:
                cur = _conn.execute(sql, args)
                rows = cur.fetchall() if cur.description else []
                _conn.commit()
            except Exception:
                _conn.rollback()
                raise
    return (rows[0] if rows else None) if one else rows


def close():
    with _lock:
        try:
            _conn.close()
        except Exception:
            pass


# ---- settings
def get_setting(key, default=None):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else default


def set_setting(key, value):
    q("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


# ---- channels
def add_channel(cid, title, username):
    q("INSERT INTO channels(id,title,username,priority) VALUES(?,?,?,0) "
      "ON CONFLICT(id) DO UPDATE SET title=excluded.title, username=excluded.username",
      (cid, title, username))


def channel_active(cid):
    """True only if the channel is currently in the active tracked list.
    Deleted channels are removed from the table, so they can be re-added as new."""
    return q("SELECT 1 AS x FROM channels WHERE id=?", (cid,), one=True) is not None


def del_channel(cid):
    q("DELETE FROM channels WHERE id=?", (cid,))


def channels():
    return q("SELECT * FROM channels ORDER BY title")


def channel(cid):
    return q("SELECT * FROM channels WHERE id=?", (cid,), one=True)


def toggle_priority(cid):
    q("UPDATE channels SET priority = 1 - priority WHERE id=?", (cid,))


# ---- keywords (table = 'keywords' or 'priority_keywords')
def kw_add(table, word):
    try:
        q(f"INSERT INTO {table}(word) VALUES(?)", (word.strip(),))
        _kw_cache.pop(table, None)
        return True
    except IntegrityError:
        return False


_kw_cache: dict = {}


def kw_list(table):
    # Cached: every channel post reads both lists; only our own writes change them.
    if table not in _kw_cache:
        _kw_cache[table] = q(f"SELECT * FROM {table} ORDER BY lower(word)")
    return _kw_cache[table]


def kw_get(table, kid):
    return q(f"SELECT * FROM {table} WHERE id=?", (kid,), one=True)


def kw_update(table, kid, word):
    try:
        q(f"UPDATE {table} SET word=? WHERE id=?", (word.strip(), kid))
        _kw_cache.pop(table, None)
        return True
    except IntegrityError:
        return False


def kw_del(table, kid):
    q(f"DELETE FROM {table} WHERE id=?", (kid,))
    _kw_cache.pop(table, None)


# ---- keyword hit statistics (for the 📊 Summary screen)
def hit_add(channel_id, channel_title, words, priority_words=(), source="text"):
    """Record one row per matched keyword. priority_words are flagged priority=1."""
    pset = {w.lower() for w in priority_words}
    seen = set()
    for w in list(priority_words) + list(words):
        if w.lower() in seen:
            continue
        seen.add(w.lower())
        q("INSERT INTO keyword_hits(channel_id,channel_title,word,priority,source) VALUES(?,?,?,?,?)",
          (channel_id, channel_title, w, 1 if w.lower() in pset else 0, source))


def top_channels(limit=10):
    return q("SELECT channel_id, MAX(channel_title) AS title, COUNT(*) AS n FROM keyword_hits "
             "GROUP BY channel_id ORDER BY n DESC LIMIT ?", (limit,))


def top_keywords(limit=10):
    return q("SELECT lower(word) AS word, COUNT(*) AS n, MAX(priority) AS priority FROM keyword_hits "
             "GROUP BY lower(word) ORDER BY n DESC LIMIT ?", (limit,))


def top_voice_keywords(limit=10):
    return q("SELECT lower(word) AS word, COUNT(*) AS n, MAX(priority) AS priority FROM keyword_hits "
             "WHERE source='voice' GROUP BY lower(word) ORDER BY n DESC LIMIT ?", (limit,))


def hit_totals():
    r = q("SELECT COUNT(*) AS total, "
          "COALESCE(SUM(CASE WHEN priority=1 THEN 1 ELSE 0 END),0) AS prio, "
          "COALESCE(SUM(CASE WHEN source='voice' THEN 1 ELSE 0 END),0) AS voice FROM keyword_hits", one=True)
    return {"total": int(r["total"] or 0), "prio": int(r["prio"] or 0), "voice": int(r["voice"] or 0)}


def hits_reset():
    q("DELETE FROM keyword_hits")
