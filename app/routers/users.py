from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import require_roles
from app.models import Role, User
from app.schemas import UserCreate, UserOut
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(
    current: User = Depends(require_roles(Role.ADMIN, Role.RESPONSABILE_DELEGHE)),
    db: Session = Depends(get_db),
) -> list[User]:
    stmt = select(User).where(User.tenant_id == current.tenant_id).order_by(User.full_name)
    return list(db.scalars(stmt))


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    request: Request,
    current: User = Depends(require_roles(Role.ADMIN)),
    db: Session = Depends(get_db),
) -> User:
    email = body.email.lower()
    if db.scalar(select(User.id).where(func.lower(User.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email gia' registrata")
    user = User(
        tenant_id=current.tenant_id,
        email=email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    db.flush()
    audit.record(
        db,
        "user_created",
        user=current,
        entity_type="user",
        entity_id=user.id,
        request=request,
        details={"email": email, "role": body.role.value},
    )
    db.commit()
    return user
