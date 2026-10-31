"""Central .env loading, database location, and environment helpers.

Imported by the entrypoint and by every cog that reads configuration, so the
values resolve identically no matter which working directory the bot was
started from.
"""

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# override=False so a real environment variable (systemd, docker, CI secret)
# always wins over a stale value left in a local .env file.
load_dotenv(BASE_DIR / ".env", override=False)

# One database, one table per feature.
DB_PATH = str(BASE_DIR / "airi.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS config(
    guild_id INTEGER PRIMARY KEY,
    prefix TEXT
);
CREATE TABLE IF NOT EXISTS liked(
    user_id INTEGER,
    song TEXT,
    url TEXT,
    duration TEXT
);
CREATE TABLE IF NOT EXISTS ai_memory(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER,
    role TEXT,
    content TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_memory_channel ON ai_memory(channel_id, id);
CREATE TABLE IF NOT EXISTS blacklist(
    user_id INTEGER PRIMARY KEY,
    reason TEXT,
    added_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS _migrations(
    name TEXT PRIMARY KEY,
    applied_at TEXT
);
"""

# Older versions kept each table in its own file. Map legacy file -> its table.
_LEGACY_FILES = {
    "config.db": "config",
    "fav_disc.db": "liked",
}


def _migrate_legacy_databases(conn):
    """Import rows from the old single-table .db files, once.

    The old files are left untouched (they are still tracked in git); a row in
    `_migrations` records that the import already ran, so restarting the bot
    cannot duplicate the data.
    """
    for filename, table in _LEGACY_FILES.items():
        legacy_path = BASE_DIR / filename
        already_done = conn.execute(
            "SELECT 1 FROM _migrations WHERE name = ?", (filename,)).fetchone()
        if already_done or not legacy_path.exists():
            continue
        # ATTACH/DETACH cannot run inside a transaction, so the connection is
        # in autocommit mode and the copy is wrapped in an explicit one: the
        # rows and the "already migrated" marker commit together or not at all.
        try:
            conn.execute("ATTACH DATABASE ? AS legacy", (str(legacy_path),))
        except sqlite3.Error as e:
            print(f"Could not open {filename}: {e}")
            continue
        try:
            exists = conn.execute(
                "SELECT 1 FROM legacy.sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            conn.execute("BEGIN")
            if exists:
                columns = ", ".join(
                    row[1] for row in conn.execute(f"PRAGMA legacy.table_info({table})")
                )
                conn.execute(
                    f"INSERT INTO main.{table} ({columns}) SELECT {columns} FROM legacy.{table}")
            conn.execute("INSERT INTO _migrations (name, applied_at) VALUES (?, datetime('now'))",
                         (filename,))
            conn.execute("COMMIT")
            if exists:
                print(f"Migrated {filename} -> airi.db ({table})")
        except sqlite3.Error as e:
            print(f"Could not migrate {filename}: {e}")
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        finally:
            conn.execute("DETACH DATABASE legacy")


def init_db():
    """Create the schema if needed and import any legacy database files."""
    # isolation_level=None -> autocommit; required for ATTACH/DETACH below.
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        conn.executescript(SCHEMA)
        _migrate_legacy_databases(conn)
    finally:
        conn.close()


def get(name, default=""):
    """Return an environment value, stripped. Missing == empty string."""
    value = os.getenv(name, default)
    return value.strip() if isinstance(value, str) else value


def first(*names):
    """Return the first non-empty value among `names`, else empty string."""
    for name in names:
        value = get(name)
        if value:
            return value
    return ""


def get_id(name):
    """Return a Discord snowflake from the environment, or None if unset/invalid."""
    raw = get(name)
    return int(raw) if raw.isdigit() else None


def get_client_id(explicit_id=None):
    """Return the Discord application / client ID from explicit id, env, or derived from bot token."""
    if explicit_id:
        return str(explicit_id)
    env_id = get("CLIENT_ID") or get("DISCORD_CLIENT_ID")
    if env_id:
        return env_id
    token = get("DISCORD_BOT_TOKEN")
    if token and "." in token:
        try:
            import base64
            part = token.split(".")[0]
            padded = part + "=" * (-len(part) % 4)
            cid = base64.b64decode(padded).decode("utf-8", errors="ignore")
            if cid.isdigit():
                return cid
        except Exception:
            pass
    return "1532368781094162472"


GITHUB_URL = "https://github.com/Sayu69420/airi2.0"

DEFAULT_BOT_INVITE_URL = (
    "https://discord.com/oauth2/authorize?client_id=1532368781094162472&permissions=8&integration_type=0&scope=bot%20applications.commands"
)


def get_bot_invite_url(client_id=None):
    """Return the Discord OAuth2 URL to invite Airi to a Discord server (Guild install)."""
    custom = get("BOT_INVITE_URL")
    if custom:
        return custom
    cid = get_client_id(client_id)
    return (
        f"https://discord.com/oauth2/authorize?client_id={cid}"
        f"&permissions=8&integration_type=0&scope=bot%20applications.commands"
    )


def get_user_install_url(client_id=None):
    """Return the Discord OAuth2 URL to install Airi to a User's Profile (Use Everywhere)."""
    custom = get("USER_INSTALL_URL")
    if custom:
        return custom
    cid = get_client_id(client_id)
    return (
        f"https://discord.com/oauth2/authorize?client_id={cid}"
        f"&integration_type=1&scope=applications.commands"
    )


def get_universal_install_url(client_id=None):
    """Return the general Discord OAuth2 authorize URL allowing server invite or user install."""
    cid = get_client_id(client_id)
    return f"https://discord.com/oauth2/authorize?client_id={cid}"


init_db()
