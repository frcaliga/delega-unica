"""Comandi di amministrazione: python -m app.cli --help"""

import typer
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Role, Tenant, User
from app.security import hash_password

cli = typer.Typer(no_args_is_help=True)


@cli.command()
def create_tenant(
    name: str = typer.Option(..., help="Denominazione dello studio o CAF"),
    codice_fiscale: str = typer.Option(..., help="Codice fiscale dell'intermediario"),
    admin_email: str = typer.Option(...),
    admin_name: str = typer.Option(...),
    admin_password: str = typer.Option(..., prompt=True, hide_input=True),
) -> None:
    """Crea uno studio con il suo primo amministratore."""
    with SessionLocal() as db:
        if db.scalar(select(Tenant.id).where(Tenant.codice_fiscale == codice_fiscale.upper())):
            raise typer.BadParameter("Esiste gia' uno studio con questo codice fiscale")
        if db.scalar(select(User.id).where(func.lower(User.email) == admin_email.lower())):
            raise typer.BadParameter("Email gia' registrata")
        tenant = Tenant(name=name, codice_fiscale=codice_fiscale.upper())
        db.add(tenant)
        db.flush()
        db.add(
            User(
                tenant_id=tenant.id,
                email=admin_email.lower(),
                full_name=admin_name,
                password_hash=hash_password(admin_password),
                role=Role.ADMIN,
            )
        )
        db.commit()
        typer.echo(f"Studio creato: {tenant.id}")


@cli.command()
def version() -> None:
    typer.echo("delega-unica 0.1.0")


if __name__ == "__main__":
    cli()
