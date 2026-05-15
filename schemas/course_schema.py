import json
from typing import Optional, List, Any
from datetime import datetime

from pydantic import BaseModel, Field, model_validator, ConfigDict


class CourseResponse(BaseModel):
    serial_no: str = Field(..., description="課程流水號 (五碼)")
    class_no: str = Field(..., description="課號 (包含系所代碼與編號)")
    title: str = Field(..., description="課程名稱")
    credit: float = Field(..., description="學分數")
    password_card: Optional[str] = Field(None, description="密碼卡需求狀態")
    teachers: List[str] = Field(default_factory=list, description="授課教師名單")
    class_times: List[str] = Field(default_factory=list, description="上課時間節次 (如 1-1 代表星期一第一節)")
    limit_cnt: Optional[int] = Field(None, description="限制選修人數")
    admit_cnt: Optional[int] = Field(None, description="已上網選上人數")
    wait_cnt: Optional[int] = Field(None, description="候補人數")
    college_id: Optional[str] = Field(None, description="隸屬學院 ID")
    department_id: Optional[str] = Field(None, description="隸屬系所 ID")
    course_type: Optional[str] = Field(None, description="修課類別 (例如 REQUIRED 必修, ELECTIVE 選修)")

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode='before')
    @classmethod
    def _parse_json_fields(cls, data: Any) -> Any:
        # data might be an SQLAlchemy ORM object or a dictionary.
        if hasattr(data, '__dict__'):
            target = data
            is_dict = False
        elif isinstance(data, dict):
            target = data
            is_dict = True
        else:
            return data

        def _parse(val):
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except json.JSONDecodeError:
                    return []
            if isinstance(val, list):
                return val
            return []

        if is_dict:
            target['teachers'] = _parse(target.get('teachers'))
            target['class_times'] = _parse(target.get('class_times'))
        else:
            if hasattr(target, 'teachers'):
                # We do not override ORM object attributes in place if not strictly needed,
                # but Pydantic from_attributes parses properties.
                # Actually, returning a dict override might be safer, or just set it:
                target.teachers = _parse(target.teachers)
            if hasattr(target, 'class_times'):
                target.class_times = _parse(target.class_times)

        return data

class CourseResult(BaseModel):
    total_count: int = Field(..., description="符合條件的課程總數")
    last_updated: Optional[datetime] = Field(None, description="資料庫最後更新時間")
    courses: List[CourseResponse] = Field(..., description="查詢到的課程列表")