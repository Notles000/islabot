from datetime import date
from sqlalchemy import (
    Column, String, Integer, Boolean, Date, DateTime, Float, ForeignKey, Text, Enum
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
import enum

Base = declarative_base()


class UserRole(str, enum.Enum):
    student    = "student"
    instructor = "instructor"
    secretaria = "secretaria"
    admin      = "admin"


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(120), nullable=False)
    email         = Column(String(200), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    role          = Column(Enum(UserRole), default=UserRole.student, nullable=False)
    year          = Column(Integer, nullable=True)          # academic year (students)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, server_default=func.now())

    enrollments   = relationship("Enrollment", back_populates="student")
    teachings     = relationship("Teaching",   back_populates="instructor")
    sessions      = relationship("ChatSession", back_populates="user")



class Program(Base):
    __tablename__ = "programs"

    id   = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, unique=True)

    courses = relationship("Course", back_populates="program")


class Semester(Base):
    __tablename__ = "semesters"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(60), nullable=False)   # e.g. "2025/26 — 1.º Semestre"
    start_date = Column(Date, nullable=False)
    end_date   = Column(Date, nullable=False)
    is_active  = Column(Boolean, default=False)

    courses    = relationship("Course", back_populates="semester")


class Course(Base):
    __tablename__ = "courses"

    id          = Column(Integer, primary_key=True, index=True)
    program_id  = Column(Integer, ForeignKey("programs.id"), nullable=True)
    semester_id = Column(Integer, ForeignKey("semesters.id"), nullable=False)
    code        = Column(String(20), nullable=False)   # e.g. "CS101"
    name        = Column(String(200), nullable=False)
    short_name  = Column(String(20), nullable=True)    # e.g. "IBD"

    program     = relationship("Program", back_populates="courses")
    semester    = relationship("Semester", back_populates="courses")
    enrollments = relationship("Enrollment", back_populates="course")
    teachings   = relationship("Teaching",   back_populates="course")
    documents   = relationship("Document",   back_populates="course")
    sessions    = relationship("ChatSession", back_populates="course")


class Enrollment(Base):
    """Student ↔ Course"""
    __tablename__ = "enrollments"

    id         = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    course_id  = Column(Integer, ForeignKey("courses.id"), nullable=False)

    student    = relationship("User",   back_populates="enrollments")
    course     = relationship("Course", back_populates="enrollments")


class Teaching(Base):
    """Instructor ↔ Course"""
    __tablename__ = "teachings"

    id            = Column(Integer, primary_key=True, index=True)
    instructor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    course_id     = Column(Integer, ForeignKey("courses.id"), nullable=False)

    instructor    = relationship("User",   back_populates="teachings")
    course        = relationship("Course", back_populates="teachings")


class Document(Base):
    __tablename__ = "documents"

    id           = Column(Integer, primary_key=True, index=True)
    course_id    = Column(Integer, ForeignKey("courses.id"), nullable=True)
    filename     = Column(String(260), nullable=False)
    filepath     = Column(String(500), nullable=False)
    doc_type     = Column(String(40), nullable=True)   # "syllabus", "lecture", "lab"
    uploaded_by  = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_at  = Column(DateTime, server_default=func.now())
    indexed      = Column(Boolean, default=False)
    checksum     = Column(String(64), nullable=True)   # MD5 of file — detects updates
    chunks_count = Column(Integer, nullable=True)      # number of vectors indexed

    course       = relationship("Course", back_populates="documents")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    course_id  = Column(Integer, ForeignKey("courses.id"), nullable=False)
    title      = Column(String(200), nullable=True)    # auto-generated from first message
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user       = relationship("User",   back_populates="sessions")
    course     = relationship("Course", back_populates="sessions")
    messages   = relationship("ChatMessage", back_populates="session", order_by="ChatMessage.id")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key   = Column(String(60), primary_key=True)
    value = Column(Text, nullable=False)


class ChatBookmark(Base):
    __tablename__ = "chat_bookmarks"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=False)
    note       = Column(String(200), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    message    = relationship("ChatMessage")


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id  = Column(Integer, ForeignKey("users.id"), primary_key=True)
    theme    = Column(String(10), default="light")
    font_size= Column(String(10), default="medium")
    language = Column(String(5), default="pt")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id               = Column(Integer, primary_key=True, index=True)
    session_id       = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    role             = Column(String(10), nullable=False)    # "user" | "assistant"
    content          = Column(Text, nullable=False)
    sources          = Column(Text, nullable=True)           # JSON: [{label, page}]
    rating           = Column(Integer, nullable=True)        # 1=thumbs up, -1=thumbs down
    retrieval_score  = Column(Float, nullable=True)          # avg relevance score of retrieved chunks
    had_results      = Column(Boolean, nullable=True)        # whether retrieval returned anything
    created_at       = Column(DateTime, server_default=func.now())

    session          = relationship("ChatSession", back_populates="messages")


class SecretariaSession(Base):
    __tablename__ = "secretaria_sessions"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    title      = Column(String(200), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user       = relationship("User")
    messages   = relationship("SecretariaMessage", back_populates="session",
                              order_by="SecretariaMessage.id")


class SecretariaMessage(Base):
    __tablename__ = "secretaria_messages"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("secretaria_sessions.id"), nullable=False)
    role       = Column(String(10), nullable=False)   # "user" | "assistant"
    content    = Column(Text, nullable=False)
    sources    = Column(Text, nullable=True)          # JSON: [{label, page, from_web}]
    created_at = Column(DateTime, server_default=func.now())

    session    = relationship("SecretariaSession", back_populates="messages")
