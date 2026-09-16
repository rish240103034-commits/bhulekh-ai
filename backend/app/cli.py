"""Operational CLI for Bhulekh-AI.

Demo seeding is disabled in production (``config.py`` forbids it), so a fresh
production database has no users at all. This provides an auditable, non-interactive
way to bootstrap the very first administrator — and to reset a locked-out one — without
re-enabling demo seeding or hand-editing the database.

Usage::

    python -m app.cli create-admin --username admin --full-name "State Admin"
    # password is read from the BHULEKH_ADMIN_PASSWORD env var, or prompted for.

    # Fully non-interactive (e.g. a provisioning script / Kubernetes Job):
    BHULEKH_ADMIN_PASSWORD='…' python -m app.cli create-admin --username admin --no-input

Passwords are never accepted on the command line (they would leak into shell history
and the process table); use the environment variable or the interactive prompt.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from app.core.database import SessionLocal
from app.core.security import (
    VALID_ROLES,
    hash_password,
    validate_password_strength,
)
from app.models.entities import User
from app.services.audit import log_action

PASSWORD_ENV = "BHULEKH_ADMIN_PASSWORD"


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _resolve_password(no_input: bool) -> str | None:
    """Password from the environment, else an interactive prompt (never from argv)."""
    password = os.environ.get(PASSWORD_ENV)
    if password:
        return password
    if no_input or not sys.stdin.isatty():
        return None
    first = getpass.getpass("New admin password: ")
    if first != getpass.getpass("Confirm password: "):
        print("error: passwords do not match", file=sys.stderr)
        return None
    return first


def create_admin(args: argparse.Namespace) -> int:
    role = args.role
    if role not in VALID_ROLES:
        return _fail(f"unknown role '{role}' (choose from {', '.join(sorted(VALID_ROLES))})")

    password = _resolve_password(args.no_input)
    if not password:
        return _fail(f"no password provided (set {PASSWORD_ENV} or run interactively)")
    try:
        validate_password_strength(password)
    except Exception as exc:  # HTTPException carries the human-readable reason
        return _fail(getattr(exc, "detail", str(exc)))

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == args.username).first()
        if existing and not args.reset_password:
            return _fail(f"user '{args.username}' already exists "
                         f"(use --reset-password to set a new password)")
        if existing:
            existing.hashed_password = hash_password(password)
            existing.is_active = True
            action = "user.password_reset"
            user = existing
        else:
            user = User(username=args.username, full_name=args.full_name or args.username,
                        role=role, hashed_password=hash_password(password))
            db.add(user)
            action = "user.created"
        db.commit()
        db.refresh(user)
        log_action(db, user.id, user.username, action, "user", user.id,
                   {"role": user.role, "via": "cli"})
        verb = "reset password for" if action == "user.password_reset" else "created"
        print(f"{verb} {role} '{user.username}' (id={user.id}).")
        return 0
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    admin = sub.add_parser("create-admin", help="Create (or reset) an administrator account.")
    admin.add_argument("--username", required=True)
    admin.add_argument("--full-name", default="")
    admin.add_argument("--role", default="admin",
                       help="Role to assign (default: admin).")
    admin.add_argument("--reset-password", action="store_true",
                       help="If the user already exists, set a new password and reactivate.")
    admin.add_argument("--no-input", action="store_true",
                       help=f"Never prompt; require {PASSWORD_ENV} to be set.")
    admin.set_defaults(func=create_admin)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
