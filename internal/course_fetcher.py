import asyncio
import logging
import httpx
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, parse_qs
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
import json

from database.models import College, Department, Course, SystemStatus
from datetime import datetime, timezone
# Note: since sqlite ON CONFLICT is different from PostgreSQL we will use a generic merge if it's not strictly postgres,
# but the prompt mentions "postgresql db", so we can assume PostgreSQL might be used.
# Since the user might be testing with SQLite (based on db_connect fallback), let's use session.merge() for simple UPSERT.

logger = logging.getLogger(__name__)

COURSE_REMOTE_URL = 'https://cis.ncu.edu.tw/Course/main/support/course.xml'
COURSE_HEADER = {
    'Accept-Language': 'zh-TW',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

async def fetch_colleges_with_departments():
    colleges = []
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.get('https://cis.ncu.edu.tw/Course/main/query/byUnion', headers=COURSE_HEADER)
        response.raise_for_status()
        
        # Use response.content (bytes) to allow BeautifulSoup to handle encoding properly
        soup = BeautifulSoup(response.content, 'html.parser')
        table_container = soup.select_one('#byUnion_table')
        if not table_container:
            logger.warning(f"Could not find '#byUnion_table' in the byUnion response. Snippet: {response.text[:500]}")
            return colleges
        
        tables = table_container.find_all('table', recursive=False)
        # Note: the html structure puts college tables directly under the main table or its tr/td
        # To be safe against nested structures, let's just find them by selector:
        tables = soup.select('#byUnion_table table')
        for i, table in enumerate(tables):
            college_id = f"collegeI{i}"
            tr1 = table.find('tr')
            if not tr1: continue
            th = tr1.find('th')
            college_name = th.get_text(strip=True) if th else f"College {i}"
            
            departments = []
            tr2 = table.find_all('tr')[1] if len(table.find_all('tr')) > 1 else None
            if tr2:
                anchors = tr2.select('td ul li a')
                for anchor in anchors:
                    href = anchor.get('href', '')
                    parsed_url = urlparse(href)
                    qs = parse_qs(parsed_url.query)
                    department_id = qs.get('dept', [''])[0]
                    
                    import re
                    # Remove trailing (count)
                    department_name = re.sub(r'\(\d+\)$', '', anchor.get_text(strip=True))
                    
                    departments.append({
                        "id": department_id,
                        "name": department_name,
                        "college_id": college_id
                    })
            
            colleges.append({
                "id": college_id,
                "name": college_name,
                "departments": departments
            })
            
    return colleges

async def fetch_course_bases(department_id: str, college_id: str):
    courses = []
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.get(COURSE_REMOTE_URL, headers=COURSE_HEADER, params={"id": department_id})
        try:
            response.raise_for_status()
        except Exception:
            # Maybe invalid department or no courses
            return courses
        
        try:
            # ET.fromstring strictly expects bytes when XML has an encoding declaration (e.g. <?xml version="1.0" encoding="UTF-8"?>)
            root = ET.fromstring(response.content)
        except Exception as e:
            logger.warning(f"Failed to parse XML for department {department_id}: {e}. Snippet: {response.text[:200]}")
            return courses
            
        for course_elem in root.findall('.//Course'):
            attr = course_elem.attrib
            class_no = attr.get('ClassNo', '')
            class_no_fmt = f"{class_no[:6]}-{class_no[6:]}" if len(class_no) > 6 else class_no
            
            teacher_str = attr.get('Teacher', '')
            teachers = [t.strip() for t in teacher_str.split(',') if t.strip()]
            
            times_str = attr.get('ClassTime', '')
            class_times = [f"{w}-{h}" for w, h in (t.split(',') for t in times_str.split(',') if len(t.split(',')) == 2)] if times_str else []
            
            courses.append({
                "serial_no": attr.get('SerialNo', '').zfill(5) if attr.get('SerialNo') else "",
                "class_no": class_no_fmt,
                "title": attr.get('Title', ''),
                "credit": float(attr.get('credit', 0) or 0),
                "password_card": attr.get('passwordCard', ''),
                "teachers": json.dumps(teachers, ensure_ascii=False),
                "class_times": json.dumps(class_times, ensure_ascii=False),
                "limit_cnt": int(attr.get('limitCnt', 0) or 0),
                "admit_cnt": int(attr.get('admitCnt', 0) or 0),
                "wait_cnt": int(attr.get('waitCnt', 0) or 0),
                "college_id": college_id,
                "department_id": department_id
            })
    return courses

async def fetch_all_course_extras():
    course_extras = []
    page_no = 1
    async with httpx.AsyncClient(verify=False) as client:
        while True:
            response = await client.get(
                'https://cis.ncu.edu.tw/Course/main/query/byKeywords', 
                headers=COURSE_HEADER,
                params={
                    'd-49489-p': page_no,
                    'query': 'true'
                }
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            trs = soup.select('#item tbody tr')
            for tr in trs:
                td1 = tr.select_one('td:nth-child(1)')
                # Extract before <br>
                serial_no = td1.contents[0].strip() if td1 and td1.contents else ""
                
                td6 = tr.select_one('td:nth-child(6)')
                raw_type = td6.get_text(strip=True) if td6 else ""
                
                course_type = "REQUIRED" if raw_type == "必修" else "ELECTIVE" if raw_type == "選修" else "UNKNOWN"
                
                course_extras.append({
                    "serial_no": serial_no.zfill(5),
                    "course_type": course_type
                })
            
            # Check next page
            pagelinks = soup.select('.pagelinks > *')
            if pagelinks and pagelinks[-1].name == 'a':
                page_no += 1
            else:
                break
                
    return course_extras

async def sync_courses_to_db(db: Session):
    """
    Main orchestration function to fetch all data and save it to the database.
    Because of large size, we commit in batches.
    """
    logger.info("Starting course fetch synchronization...")
    try:
        colleges = await fetch_colleges_with_departments()
        logger.info(f"Fetched {len(colleges)} colleges.")
        
        all_courses = []
        for c in colleges:
            # UPSERT College
            db_college = db.query(College).filter(College.id == c['id']).first()
            if not db_college:
                db_college = College(id=c['id'], name=c['name'])
                db.add(db_college)
            else:
                db_college.name = c['name']
            
            for d in c['departments']:
                # UPSERT Department
                db_dept = db.query(Department).filter(Department.id == d['id']).first()
                if not db_dept:
                    db_dept = Department(id=d['id'], name=d['name'], college_id=c['id'])
                    db.add(db_dept)
                else:
                    db_dept.name = d['name']
                    db_dept.college_id = c['id']
                
                # Fetch courses for department
                dept_courses = await fetch_course_bases(d['id'], c['id'])
                all_courses.extend(dept_courses)
                
            # commit at college boundary
            db.commit()
            
        logger.info(f"Fetched {len(all_courses)} total courses from XML.")
        
        # Deduplicate locally to prevent UniqueViolation for cross-listed courses 
        # in the same uncommitted transaction batch.
        unique_courses = {}
        for cd in all_courses:
            if cd.get('serial_no'):
                unique_courses[cd['serial_no']] = cd
                
        # Merge all unique courses into DB
        for cd in unique_courses.values():
            db_course = db.query(Course).filter(Course.serial_no == cd['serial_no']).first()
            if not db_course:
                db_course = Course(**cd)
                db.add(db_course)
            else:
                for k, v in cd.items():
                    setattr(db_course, k, v)
        db.commit()
        
        logger.info("Starting course extras fetch...")
        extras = await fetch_all_course_extras()
        logger.info(f"Fetched {len(extras)} course extras.")
        
        # Merge extras
        for extra in extras:
            db_course = db.query(Course).filter(Course.serial_no == extra['serial_no']).first()
            if db_course:
                db_course.course_type = extra['course_type']
        
        
        status = db.query(SystemStatus).filter(SystemStatus.id == 1).first()
        if not status:
            status = SystemStatus(id=1, last_course_sync=datetime.now(timezone.utc))
            db.add(status)
        else:
            status.last_course_sync = datetime.now(timezone.utc)
        
        db.commit()
        logger.info("Course synchronization completed successfully.")
        
    except Exception as e:
        logger.error(f"Error during course sync: {e}")
        db.rollback()
