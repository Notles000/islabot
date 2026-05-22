from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ..auth import verify_password, create_token, hash_password
from ..database import get_db
from ..models import User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterBody(BaseModel):
    name:     str
    email:    EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user_id:      int
    name:         str
    role:         str


@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username, User.is_active == True).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")

    return TokenOut(
        access_token=create_token(user.id, user.role),
        user_id=user.id,
        name=user.name,
        role=user.role,
    )


@router.post("/register", response_model=TokenOut, status_code=201)
def register(body: RegisterBody, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email já registado")

    user = User(
        name=body.name,
        email=body.email,
        password_hash=hash_password(body.password),
        role=UserRole.student,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return TokenOut(
        access_token=create_token(user.id, user.role),
        user_id=user.id,
        name=user.name,
        role=user.role,
    )
