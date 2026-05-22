"""Run once to create the admin user and real ISLA CTeSP IA courses."""

import sys
import os
import json
sys.path.insert(0, os.path.dirname(__file__))

from backend.database import engine, SessionLocal
from backend.models import Base, User, UserRole, Semester, Course, Enrollment, Teaching
from backend.auth import hash_password
from datetime import date

Base.metadata.create_all(bind=engine)
db = SessionLocal()

# ── Users from users.json ─────────────────────────────────────────────────────
_users_file = os.path.join(os.path.dirname(__file__), "users.json")
with open(_users_file) as f:
    _users = json.load(f)

for u in _users:
    if not db.query(User).filter(User.email == u["email"]).first():
        db.add(User(
            name=u["name"],
            email=u["email"],
            password_hash=hash_password(u["password"]),
            role=UserRole(u["role"]),
        ))
        print(f"✓ Utilizador criado: {u['email']} ({u['role']})")

# ── Semesters ─────────────────────────────────────────────────────────────────
sem1 = db.query(Semester).filter(Semester.name == "2025/26 — 1.º Semestre").first()
if not sem1:
    sem1 = Semester(
        name="2025/26 — 1.º Semestre",
        start_date=date(2025, 9, 15),
        end_date=date(2026, 1, 31),
        is_active=False,
    )
    db.add(sem1)
    db.flush()
    print("✓ 1.º Semestre criado")

sem2 = db.query(Semester).filter(Semester.name == "2025/26 — 2.º Semestre").first()
if not sem2:
    sem2 = Semester(
        name="2025/26 — 2.º Semestre",
        start_date=date(2026, 2, 16),
        end_date=date(2026, 7, 31),
        is_active=True,
    )
    db.add(sem2)
    db.flush()
    print("✓ 2.º Semestre criado")

# ── CTeSP Inteligência Artificial — 1.º Semestre ──────────────────────────────
courses_sem1 = [
    ("IIA",  "Introdução à Inteligência Artificial", "IIA"),
    ("FCSI", "Fundamentos e Conceção de Sistemas de Informação", "FCSI"),
]
for code, name, short in courses_sem1:
    if not db.query(Course).filter(Course.code == code, Course.semester_id == sem1.id).first():
        db.add(Course(semester_id=sem1.id, code=code, name=name, short_name=short))
        print(f"✓ UC {code} criada (1.º sem)")

# ── CTeSP Inteligência Artificial — 2.º Semestre ──────────────────────────────
courses_sem2 = [
    ("ESIA", "Engenharia de Software para IA", "ESIA"),
    ("EST",  "Estatística", "EST"),
]
for code, name, short in courses_sem2:
    if not db.query(Course).filter(Course.code == code, Course.semester_id == sem2.id).first():
        db.add(Course(semester_id=sem2.id, code=code, name=name, short_name=short))
        print(f"✓ UC {code} criada (2.º sem)")

db.commit()

print("\n─────────────────────────────────")
for u in _users:
    print(f"{u['role'].capitalize()}: {u['email']} / {u['password']}")
print("─────────────────────────────────")
print("\nNota: coloca os PDFs das UCs em data/courses/<CODE>/")
print("e corre: python ingest.py")
db.close()
