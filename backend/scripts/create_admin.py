#!/usr/bin/env python3
"""
Creates an Admin account (see the Admin model / admin_access_required in
app.py) directly in the database. Deliberately not a web route — unlike
Employer, Admin has no self-registration endpoint, since anyone being able
to create their own admin account would defeat the point of it being a
gated role. This mirrors backup.py/restore.py: an operator-run script, not
a web-exposed capability.

Usage:
    python scripts/create_admin.py --email you@org.org --name "Jane Doe"
    (prompts for a password interactively, so it never ends up in shell
    history or process listings)
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--role",
        default="admin",
        choices=["admin", "verifier"],
        help="'admin' can manage other admin accounts (/admin/accounts) and view analytics; "
             "'verifier' can only view analytics (/admin/analytics) — see the Admin model docstring in app.py",
    )
    args = parser.parse_args()

    with app_module.app.app_context():
        existing = app_module.Admin.query.filter_by(email=args.email).first()
        if existing:
            print(f"An admin account already exists for {args.email}.", file=sys.stderr)
            sys.exit(1)

        password = getpass.getpass("Password (min 8 characters): ")
        if len(password) < 8:
            print("Password must be at least 8 characters.", file=sys.stderr)
            sys.exit(1)
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords did not match.", file=sys.stderr)
            sys.exit(1)

        admin = app_module.Admin(
            name=args.name,
            email=args.email,
            role=args.role,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
        print(f"Created admin account for {args.email} (role={args.role}).")


if __name__ == "__main__":
    main()
