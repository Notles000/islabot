import os, shutil
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user, require_role
from ..database import get_db
from ..models import User, Course, Enrollment, Document, Semester, UserRole
from ..config import settings

router = APIRouter(prefix="/courses", tags=["courses"])


class CourseOut(BaseModel):
    id:         int
    code:       str
    name:       str
    short_name: str | None
    semester:   str

    class Config:
        from_attributes = True


@router.get("/mine", response_model=List[CourseOut])
def my_courses(db: Session = Depends(get_db), current: User = Depends(get_current_user)):
    """Return courses the current user can access."""
    if current.role == UserRole.admin:
        courses = db.query(Course).all()
    elif current.role == UserRole.student:
        enrollments = db.query(Enrollment).filter(Enrollment.student_id == current.id).all()
        course_ids  = [e.course_id for e in enrollments]
        courses = db.query(Course).filter(Course.id.in_(course_ids)).all()
    else:
        from ..models import Teaching
        teachings  = db.query(Teaching).filter(Teaching.instructor_id == current.id).all()
        course_ids = [t.course_id for t in teachings]
        courses = db.query(Course).filter(Course.id.in_(course_ids)).all()

    return [
        CourseOut(
            id=c.id, code=c.code, name=c.name, short_name=c.short_name,
            semester=c.semester.name if c.semester else "",
        )
        for c in courses
    ]


@router.post("/{course_id}/documents", status_code=201)
async def upload_document(
    course_id: int,
    doc_type:  str  = Form("lecture"),
    file:      UploadFile = File(...),
    db:        Session    = Depends(get_db),
    current:   User       = Depends(require_role(UserRole.instructor, UserRole.admin)),
):
    course = db.query(Course).filter(Course.id == course_id).first()
    if not course:
        raise HTTPException(status_code=404, detail="UC não encontrada")

    # Save file to disk
    dest_dir = os.path.join(settings.docs_path, str(course.code))
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, file.filename)

    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Persist record (indexing now done via admin panel)
    doc = Document(
        course_id=course.id,
        filename=file.filename,
        filepath=dest_path,
        doc_type=doc_type,
        uploaded_by=current.id,
        indexed=False,
    )
    db.add(doc)
    db.commit()

    return {"filename": file.filename, "status": "uploaded"}
