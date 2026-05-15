from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, DateTime
from sqlalchemy.orm import relationship
from database.db_connect import Base

class College(Base):
    __tablename__ = "colleges"
    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)

    departments = relationship("Department", back_populates="college")

class Department(Base):
    __tablename__ = "departments"
    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    college_id = Column(String, ForeignKey("colleges.id"), nullable=False)

    college = relationship("College", back_populates="departments")
    courses = relationship("Course", back_populates="department")

class Course(Base):
    __tablename__ = "courses"
    serial_no = Column(String, primary_key=True, index=True)
    class_no = Column(String, index=True, nullable=False)
    title = Column(String, nullable=False)

    credit = Column(Float, nullable=False)
    password_card = Column(String, nullable=True)

    teachers = Column(Text, nullable=True) # comma-separated or JSON
    class_times = Column(Text, nullable=True) # comma-separated or JSON

    limit_cnt = Column(Integer, nullable=True)
    admit_cnt = Column(Integer, nullable=True)
    wait_cnt = Column(Integer, nullable=True)

    college_id = Column(String, ForeignKey("colleges.id"), nullable=True)
    department_id = Column(String, ForeignKey("departments.id"), nullable=True)
    course_type = Column(String, nullable=True) # e.g. REQUIRED, ELECTIVE

    department = relationship("Department", back_populates="courses")
    college = relationship("College")

class SystemStatus(Base):
    __tablename__ = "system_status"
    id = Column(Integer, primary_key=True, index=True)
    last_course_sync = Column(DateTime(timezone=True), nullable=True)
