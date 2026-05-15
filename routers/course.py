from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session
from database.db_connect import get_db
from database.models import Course, SystemStatus
from internal.course_fetcher import sync_courses_to_db
from schemas.course_schema import CourseResult
import logging

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/course",
    tags=['Courses']
)

async def run_sync_task():
    db = next(get_db())
    try:
        logger.info("Manual course sync started from endpoint.")
        await sync_courses_to_db(db)
    except Exception as e:
        logger.error(f"Error in manual sync task: {e}")
    finally:
        db.close()

@router.post('/sync', 
             summary="Trigger Course Synchronization", 
             description="手動觸發背景作業，將中央大學 (NCU) 的所有課程資料從遠端同步至本地資料庫。該操作不會阻塞連線。",
             response_description="回傳同步任務的啟動狀態")
async def manual_sync_courses(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_sync_task)
    return {"status": "sync_started", "message": "Course synchronization has started in the background."}

@router.get('', 
            response_model=CourseResult, 
            summary="Query Courses", 
            description="透過各種自訂條件 (如課號、課程名稱、學院 ID、系所 ID) 來檢索資料庫中的所有課程。支援分頁功能 (利用 skip, limit) 以及關鍵字模糊搜尋。",
            response_description="包含所查詢之課程列表，以及符合該條件的資料總數。")
async def query_courses(
    title: Optional[str] = Query(None, description="以課程名稱進行模糊搜尋 (例如輸入 '程式' 將找出所有包含程式兩字的課程)"),
    class_no: Optional[str] = Query(None, description="以課號進行模糊搜尋 (包含系縮寫/數字代碼)"),
    serial_no: Optional[str] = Query(None, description="指定特定的課程流水號查尋單一課程 (五碼)"),
    department_id: Optional[str] = Query(None, description="過濾特定「系所」開設的課程"),
    college_id: Optional[str] = Query(None, description="過濾特定「學院」開設的課程"),
    course_type: Optional[str] = Query(None, description="依據修課類別搜尋，如 REQUIRED (必修), ELECTIVE (選修)"),
    skip: int = Query(0, ge=0, description="跳過前 N 筆資料，用於分頁"),
    limit: int = Query(100, ge=1, le=1000, description="限制回傳的資料筆數 (最多一次 1000 筆)"),
    db: Session = Depends(get_db)
):
    query = db.query(Course)

    if title:
        query = query.filter(Course.title.ilike(f"%{title}%"))
    if class_no:
        query = query.filter(Course.class_no.ilike(f"%{class_no}%"))
    if serial_no:
        query = query.filter(Course.serial_no == serial_no)
    if department_id:
        query = query.filter(Course.department_id == department_id)
    if college_id:
        query = query.filter(Course.college_id == college_id)
    if course_type:
        query = query.filter(Course.course_type == course_type)

    total_count = query.count()
    courses = query.offset(skip).limit(limit).all()

    status = db.query(SystemStatus).filter(SystemStatus.id == 1).first()
    last_updated = status.last_course_sync if status else None

    return CourseResult(
        total_count=total_count,
        last_updated=last_updated,
        courses=courses
    )