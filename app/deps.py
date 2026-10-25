import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Role, User
from app.security import decode_token

_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    user: User
    mfa: bool

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.user.tenant_id


def _unauthorized(detail: str = "Credenziali non valide") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def get_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> AuthContext:
    """Utente autenticato, anche se non ha ancora completato il secondo fattore."""
    if credentials is None:
        raise _unauthorized()
    try:
        payload = decode_token(credentials.credentials, "access")
    except jwt.InvalidTokenError as exc:
        raise _unauthorized() from exc
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.active or str(user.tenant_id) != payload["tid"]:
        raise _unauthorized()
    return AuthContext(user=user, mfa=bool(payload.get("mfa")))


def get_current_user(auth: AuthContext = Depends(get_auth)) -> User:
    """Utente pienamente autenticato: richiesto da tutte le API applicative."""
    if get_settings().require_mfa and not auth.mfa:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Secondo fattore di autenticazione richiesto"
        )
    return auth.user


def require_roles(*roles: Role) -> Callable[[User], User]:
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Permessi insufficienti")
        return user

    return checker
