"""Operator commands. They need shell access to the server, by design: platform roles can never be granted over the API,
so a stolen web session cannot mint new platform admins.

    uv run python -m app.cli create-platform-admin --email ops@lygo.co.ke --name "Ops"
    uv run python -m app.cli create-platform-admin --email support@lygo.co.ke --role platform_support
    uv run python -m app.cli list-platform-users
    uv run python -m app.cli revoke-platform-role --email ops@lygo.co.ke

In a container: docker compose exec api python -m app.cli create-platform-admin --email ...
"""
import argparse
import asyncio
import getpass
import sys
import uuid

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select

from app.core.security import MIN_PASSWORD_LEN, hash_password
from app.core.tenancy import TenantContext, tenant_session
from app.db.models.tenancy import User
from app.db.session import make_sessionmaker
from app.domain.audit import service as audit

PLATFORM_ROLES = ("platform_admin", "platform_support")


class CliError(Exception): ...


def _password_problem(pw: str) -> str | None:
    if len(pw) < MIN_PASSWORD_LEN:
        return f"Use at least {MIN_PASSWORD_LEN} characters."
    if len(set(pw)) < 5:
        return "That password is too repetitive."
    return None


def _read_password(from_stdin: bool) -> str:
    if from_stdin:  # for automation: printf '%s' "$PW" | python -m app.cli ... --password-stdin
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("Password for the new account: ")
        if getpass.getpass("Repeat password: ") != pw:
            raise CliError("The passwords don't match.")
    if problem := _password_problem(pw):
        raise CliError(problem)
    return pw


def _email(raw: str) -> str:
    try:
        return validate_email(raw.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise CliError(f"Not a valid email address: {exc}") from exc


async def _audit(factory, actor: uuid.UUID, action: str, target: uuid.UUID, meta: dict[str, str]) -> None:  # type: ignore[no-untyped-def]
    ctx = TenantContext(firm_id=None, user_id=actor, role="platform_admin")  # firm-less platform event
    async with tenant_session(factory, ctx) as db:
        await audit.record(db, ctx, action, target=f"user:{target}", meta=meta | {"via": "cli"})


async def grant(email: str, role: str, name: str, password_stdin: bool) -> str:
    factory = make_sessionmaker()
    email = _email(email)
    async with factory() as db, db.begin():
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is not None:
            if user.platform_role == role:
                return f"{email} is already {role}. Nothing changed."
            previous = user.platform_role or "none"
            user.platform_role = role
            uid, created = user.id, False
        else:
            pw = _read_password(password_stdin)
            user = User(email=email, password_hash=hash_password(pw), name=name.strip(), platform_role=role)
            db.add(user)
            await db.flush()
            uid, created, previous = user.id, True, "none"
    await _audit(factory, uid, "platform_role.grant", uid, {"role": role, "previous": previous})
    if created:
        return f"Created {email} as {role}. Sign in at the web app; set up two-step sign-in straight away."
    return f"{email} is now {role} (was {previous}). They must sign in again for it to take effect."


async def revoke(email: str) -> str:
    factory = make_sessionmaker()
    email = _email(email)
    async with factory() as db, db.begin():
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            raise CliError(f"No account with email {email}.")
        if user.platform_role is None:
            return f"{email} has no platform role. Nothing changed."
        previous, user.platform_role, uid = user.platform_role, None, user.id
    await _audit(factory, uid, "platform_role.revoke", uid, {"previous": previous})
    return f"Removed {previous} from {email}."


async def list_users() -> str:
    async with make_sessionmaker()() as db:
        rows = (await db.execute(
            select(User.email, User.name, User.platform_role, User.mfa_totp_enabled_at).where(User.platform_role.is_not(None)).order_by(User.email)
        )).all()
    if not rows:
        return "No platform users yet. Create one with: python -m app.cli create-platform-admin --email you@example.com"
    return "\n".join(f"{role:17} {email:40} {name or '':25} mfa={'on' if mfa else 'OFF'}" for email, name, role, mfa in rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Lygo operator commands.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-platform-admin", help="Create a platform admin (or promote an existing account).")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="")
    c.add_argument("--role", choices=PLATFORM_ROLES, default="platform_admin", help="platform_support can view but not decide.")
    c.add_argument("--password-stdin", action="store_true", help="Read the new account's password from stdin instead of prompting.")
    r = sub.add_parser("revoke-platform-role", help="Remove a platform role from an account (the account itself stays).")
    r.add_argument("--email", required=True)
    sub.add_parser("list-platform-users", help="List accounts with a platform role.")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "create-platform-admin":
            out = asyncio.run(grant(args.email, args.role, args.name, args.password_stdin))
        elif args.cmd == "revoke-platform-role":
            out = asyncio.run(revoke(args.email))
        else:
            out = asyncio.run(list_users())
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
