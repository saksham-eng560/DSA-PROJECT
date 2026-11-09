#!/usr/bin/env python3
"""Find your account and reset its password (local mode, the SQLite database ``./start.sh`` uses).

    backend/.venv/bin/python scripts/account.py where                 # which database, which accounts
    backend/.venv/bin/python scripts/account.py reset-password you@example.com

"Invalid email or password" usually means the app is reading a different database from the one
your account is in: every copy of the project folder has its own ``backend/data/autoapply.db``.
``where`` lists the accounts in this copy's database and looks for other copies next to it.
The new password is typed at a hidden prompt (never on the command line).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "backend" / "data" / "autoapply.db"


def database_path() -> Path:
    """The SQLite file ./start.sh uses (LOCAL_DATABASE_URL overrides it, like in start.sh)."""
    url = os.environ.get("LOCAL_DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return Path(url.removeprefix("sqlite:///"))
    if url:
        sys.exit(f"LOCAL_DATABASE_URL points at {url.split('@')[-1]}, not SQLite: use your database's own tools there.")
    return DB


def accounts(path: Path) -> list[tuple[str, str]]:
    """(email, created_at) of every account in a database file; [] when it has none or isn't ours."""
    if not path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            return [(r[0], str(r[1] or "")[:10]) for r in conn.execute("SELECT email, created_at FROM users ORDER BY created_at")]
    except sqlite3.Error:
        return []


def other_copies() -> list[Path]:
    """Databases of other copies of the project nearby (a copy inside this one, or this one's parent)."""
    candidates = {ROOT / "autoapply-ai" / "backend" / "data" / "autoapply.db",
                  ROOT.parent / "backend" / "data" / "autoapply.db"}
    candidates |= set(ROOT.parent.glob("*/backend/data/autoapply.db"))
    return sorted(p.resolve() for p in candidates if p.is_file() and p.resolve() != database_path().resolve())


def where(email: str | None) -> None:
    path = database_path()
    found = accounts(path)
    print(f"This copy: {ROOT}")
    print(f"Database ./start.sh uses: {path}" + ("" if path.is_file() else "  (doesn't exist yet: starts empty)"))
    if found:
        print(f"Accounts in it ({len(found)}):")
        for addr, created in found:
            print(f"  {addr}   created {created}")
    else:
        print("No accounts in it: sign up at http://localhost:3000/register, or use the copy listed below.")
    for other in other_copies():
        others = accounts(other)
        if others:
            print(f"\nAnother copy's database: {other}")
            for addr, created in others:
                print(f"  {addr}   created {created}")
            print(f"  -> to use it, start the app from {other.parents[2]}")
    if email:
        here = any(a.lower() == email.lower() for a, _ in found)
        print(f"\n{email}: " + ("is in this database. If sign-in still fails, reset the password:\n"
                               f"  backend/.venv/bin/python scripts/account.py reset-password {email}"
                               if here else "is NOT in this database."))


def reset_password(email: str) -> None:
    path = database_path()
    if not any(a.lower() == email.lower() for a, _ in accounts(path)):
        sys.exit(f"{email} isn't in {path}. Run `scripts/account.py where {email}` to find it.")
    first = getpass.getpass("New password (8+ characters): ")
    if len(first) < 8:
        sys.exit("Too short: use at least 8 characters.")
    if getpass.getpass("Type it again: ") != first:
        sys.exit("The two passwords don't match; nothing changed.")
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.security import hash_password  # only needed here

    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE users SET hashed_password = ? WHERE lower(email) = ?", (hash_password(first), email.lower()))
    print(f"Password changed for {email}. Sign in at http://localhost:3000/login (restart ./start.sh if it isn't running).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("where", help="show which database the app uses and the accounts in it")
    w.add_argument("email", nargs="?")
    r = sub.add_parser("reset-password", help="set a new password (typed at a hidden prompt)")
    r.add_argument("email")
    args = parser.parse_args()
    if args.command == "where":
        where(args.email)
    else:
        reset_password(args.email)


if __name__ == "__main__":
    main()
