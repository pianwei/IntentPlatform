"""One-time operator bootstrap; never resets an existing account or prints secrets."""
import argparse
import getpass
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import engine
from .identity_models import LocalAccount
from .local_identity import platform_ids, secret_hash, identity_audit
from .config import settings


def main():
    parser = argparse.ArgumentParser(description="Initialize an explicitly configured local platform administrator")
    parser.add_argument("--user-id", required=True, help="Stable subject in INTENT_PLATFORM_ADMINS_JSON; may explicitly bind an existing subject")
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args()
    from .identity_schemas import AccountCreate
    body = AccountCreate(user_id=args.user_id, username=args.username, display_name=args.display_name)
    if settings().identity_mode != "local" or args.user_id not in platform_ids() or args.user_id.startswith("guest_"):
        raise SystemExit("Configure this stable subject as a local platform administrator before bootstrap")
    password = getpass.getpass("New administrator password (at least 12 characters): ")
    if not 12 <= len(password) <= 256 or password != getpass.getpass("Repeat password: "):
        raise SystemExit("Password length or confirmation is invalid")
    with Session(engine) as db, db.begin():
        from sqlalchemy import text
        db.execute(text("SELECT pg_advisory_xact_lock(9102220)"))
        if db.get(LocalAccount, args.user_id) or db.scalar(select(LocalAccount.user_id).where(LocalAccount.username == body.username.lower())):
            raise SystemExit("Account already exists; bootstrap never resets credentials")
        account = LocalAccount(user_id=args.user_id, username=body.username.lower(), display_name=body.display_name.strip(),
                               password_hash=secret_hash(password), must_change_password=False)
        db.add(account); identity_audit(db, "operator-bootstrap", args.user_id, "account.bootstrap")
    print("Local administrator initialized; historical sessions and releases were not changed")


if __name__ == "__main__":
    main()
