import os
import re
import calendar
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import certifi
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from bson.json_util import dumps

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000
STATUSES = {"PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"}
PRESENT_STATUSES = {"PRESENT", "WFH", "ON_DUTY"}

client = MongoClient(
    os.getenv("MONGO_URI", "mongodb://localhost:27017"),
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=5000,
)
db = client[os.getenv("MONGO_DB", "attendance_db")]
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def create_indexes():
    db.employees.create_index("emp_code", unique=True)
    db.employees.create_index(
        [("department", ASCENDING), ("joined_on", ASCENDING)],
        name="department_joined_on_idx",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)]
    )
    db.attendance_logs.create_index(
        [("status", ASCENDING), ("date", DESCENDING)]
    )


def now_ist():
    return datetime.now(IST).replace(microsecond=0)


def epoch_ms(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def parse_epoch(value):
    if value is None:
        return now_ist()
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(422, "Timestamp must be integer epoch milliseconds")
    if not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
        raise HTTPException(422, "Timestamp is outside the allowed range")
    return datetime.fromtimestamp(value / 1000, UTC).replace(
        microsecond=0
    ).astimezone(IST)


def as_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST).replace(microsecond=0)


def round_half_up(value, places=2):
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", value
    ):
        raise HTTPException(422, "Date must use YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "Invalid calendar date")


def parse_month(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise HTTPException(422, "Month must use YYYY-MM")
    try:
        return date.fromisoformat(value + "-01")
    except ValueError:
        raise HTTPException(422, "Invalid month")


def month_bounds(month):
    first = parse_month(month)
    last = date(
        first.year,
        first.month,
        calendar.monthrange(first.year, first.month)[1],
    )
    return first, last


def valid_shift(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d", value
    ):
        raise ValueError("Time must use HH:MM")
    return value


def shift_datetime(day, shift_time):
    hour, minute = map(int, shift_time.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=IST)


def shift_date_for_punch(punched_at, employee):
    local = as_ist(punched_at)
    if (
        employee["shift_end"] <= employee["shift_start"]
        and local.strftime("%H:%M") < employee["shift_end"]
    ):
        return local.date() - timedelta(days=1)
    return local.date()



def compute_late_minutes(punch_in, shift_start, shift_date=None):
    local = as_ist(punch_in)
    day = shift_date or local.date()
    start = shift_datetime(day, shift_start)

    # For an overnight shift, the attendance date may be
    # the previous day when the punch occurs after midnight.
    if shift_date is None and start > local:
        start -= timedelta(days=1)

    elapsed = int((local - start).total_seconds())

    # The first 10 minutes are a grace period.
    return elapsed // 60 if elapsed > 600 else 0



def compute_work_hours(punch_in, punch_out):
    seconds = (as_ist(punch_out) - as_ist(punch_in)).total_seconds()
    return round_half_up(seconds / 3600, 2)


def compute_overtime(punch_out, shift_end, date_str, shift_start="09:30"):
    day = parse_date(date_str)
    end = shift_datetime(day, shift_end)
    if shift_end <= shift_start:
        end += timedelta(days=1)
    seconds = (as_ist(punch_out) - end).total_seconds()
    return int(seconds // 60) if seconds >= 1800 else 0


def working_days(start, end):
    if start > end:
        return 0
    days = (end - start).days + 1
    full_weeks, remainder = divmod(days, 7)
    count = full_weeks * 5
    for offset in range(remainder):
        if (start.weekday() + offset) % 7 < 5:
            count += 1
    return count


def employee_or_404(emp_code):
    employee = db.employees.find_one({"emp_code": emp_code})
    if not employee:
        raise HTTPException(404, "Employee not found")
    return employee


def attendance_or_404(emp_code, date_str):
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date_str})
    if not record:
        raise HTTPException(404, "Attendance record not found")
    return record



def clean_doc(doc):
    if doc is None:
        return None

    result = dict(doc)
    result.pop("_id", None)
    result.pop("id", None)

    for key in ("punch_in", "punch_out", "created_at"):
        if isinstance(result.get(key), datetime):
            result[key] = epoch_ms(result[key])

    for item in result.get("history", []):
        if isinstance(item.get("at"), datetime):
            item["at"] = epoch_ms(item["at"])

        for change in item.get("changes", {}).values():
            for side in ("from", "to"):
                if isinstance(change.get(side), datetime):
                    change[side] = epoch_ms(change[side])

    return result



def attendance_day(punch_in, employee):
    return shift_date_for_punch(punch_in, employee).isoformat()


def validate_status(status):
    if status not in STATUSES:
        raise HTTPException(422, "Unsupported attendance status")
    return status


def record_changes(old, new, fields):
    changes = {}
    for field in fields:
        before, after = old.get(field), new.get(field)
        if field in ("punch_in", "punch_out"):
            before = epoch_ms(before) if before else None
            after = epoch_ms(after) if after else None
        if before != after:
            changes[field] = {"from": before, "to": after}
    return changes


class EmployeeIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=120)
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("email")
    @classmethod
    def check_email(cls, value):
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email")
        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def check_shift(cls, value):
        return valid_shift(value)

    @field_validator("joined_on")
    @classmethod
    def check_joined_on(cls, value):
        parse_date(value)
        return value

    @model_validator(mode="after")
    def shifts_differ(self):
        if self.shift_start == self.shift_end:
            raise ValueError("Shift start and end must differ")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: str = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def check_timestamp(cls, value):
        if value is not None and not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
            raise ValueError("Timestamp outside allowed range")
        return value


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None

    @field_validator("punched_at")
    @classmethod
    def check_timestamp(cls, value):
        if value is not None and not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
            raise ValueError("Timestamp outside allowed range")
        return value


class RegularizeIn(BaseModel):
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)
    status: Optional[str] = None
    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None

    @field_validator("punch_in", "punch_out")
    @classmethod
    def check_timestamp(cls, value):
        if value is not None and not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
            raise ValueError("Timestamp outside allowed range")
        return value


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(503, "MongoDB unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    document = body.model_dump()
    document["created_at"] = now_ist().astimezone(UTC)
    try:
        result = db.employees.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(409, "Employee code already exists")
    return clean_doc(db.employees.find_one({"_id": result.inserted_id}))


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {"department": department} if department else {}
    total = db.employees.count_documents(query)
    docs = (
        db.employees.find(query)
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [clean_doc(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = employee_or_404(body.emp_code)
    status = validate_status(body.status)
    if status not in PRESENT_STATUSES:
        raise HTTPException(422, "Punch-in requires a presence status")
    punched = parse_epoch(body.punched_at)
    day = attendance_day(punched, employee)
    document = {
        "emp_code": body.emp_code,
        "date": day,
        "status": status,
        "punch_in": punched.astimezone(UTC),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            punched, employee["shift_start"], parse_date(day)
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        result = db.attendance_logs.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(409, "Attendance already exists for this date")
    return clean_doc(db.attendance_logs.find_one({"_id": result.inserted_id}))


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee_or_404(body.emp_code)
    punched = parse_epoch(body.punched_at)
    punched_utc = punched.astimezone(UTC)
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punched_utc},
            "punch_out": None,
            "status": {"$in": list(PRESENT_STATUSES)},
        },
        sort=[("punch_in", DESCENDING)],
    )
    if not record:
        closed = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_in": {"$lte": punched_utc},
                "punch_out": {"$ne": None},
                "status": {"$in": list(PRESENT_STATUSES)},
            }
        )
        if closed:
            raise HTTPException(409, "Attendance already punched out")
        raise HTTPException(404, "No open punch-in found")
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    punch_start = as_ist(record["punch_in"])
    seconds = (punched - punch_start).total_seconds()
    if seconds <= 0 or seconds > 86400:
        raise HTTPException(
            422, "Punch-out must be after punch-in and within 24 hours"
        )
    work_hours = compute_work_hours(punch_start, punched)
    overtime = compute_overtime(
        punched,
        employee["shift_end"],
        record["date"],
        employee["shift_start"],
    )
    result = db.attendance_logs.update_one(
        {"_id": record["_id"], "punch_out": None},
        {"$set": {
            "punch_out": punched_utc,
            "work_hours": work_hours,
            "overtime_minutes": overtime,
            "half_day": work_hours < 4.50,
        }},
    )
    if result.modified_count != 1:
        raise HTTPException(409, "Attendance was updated concurrently")
    return clean_doc(db.attendance_logs.find_one({"_id": record["_id"]}))


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}
    if emp_code:
        query["emp_code"] = emp_code
    if date_from or date_to:
        date_filter = {}
        if date_from:
            parse_date(date_from)
            date_filter["$gte"] = date_from
        if date_to:
            parse_date(date_to)
            date_filter["$lte"] = date_to
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from must not exceed date_to")
        query["date"] = date_filter
    if status:
        query["status"] = validate_status(status)
    total = db.attendance_logs.count_documents(query)
    docs = (
        db.attendance_logs.find(query)
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [clean_doc(doc) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.patch("/attendance/{emp_code}/{date_str}")
def regularize_attendance(emp_code: str, date_str: str, body: RegularizeIn):
    employee = employee_or_404(emp_code)
    day = parse_date(date_str).isoformat()
    old = attendance_or_404(emp_code, day)
    if body.status is not None:
        validate_status(body.status)

    updated = dict(old)
    if body.status is not None:
        updated["status"] = body.status

    if updated["status"] in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                422, "ABSENT/LEAVE cannot have supplied punch times"
            )
        updated.update({
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        })
    else:
        pin = (
            parse_epoch(body.punch_in)
            if body.punch_in is not None
            else as_ist(old.get("punch_in"))
        )
        pout = (
            parse_epoch(body.punch_out)
            if body.punch_out is not None
            else as_ist(old.get("punch_out"))
        )
        if pin is None:
            raise HTTPException(422, "Presence status requires punch_in")
        if attendance_day(pin, employee) != day:
            raise HTTPException(422, "Punch-in does not match attendance date")
        if pout is not None:
            seconds = (pout - pin).total_seconds()
            if seconds <= 0 or seconds > 86400:
                raise HTTPException(
                    422,
                    "Punch-out must be within 24 hours after punch-in",
                )
        updated["punch_in"] = pin.astimezone(UTC)
        updated["punch_out"] = pout.astimezone(UTC) if pout else None
        updated["late_minutes"] = compute_late_minutes(
            pin, employee["shift_start"], parse_date(day)
        )
        if pout:
            updated["work_hours"] = compute_work_hours(pin, pout)
            updated["overtime_minutes"] = compute_overtime(
                pout, employee["shift_end"], day, employee["shift_start"]
            )
            updated["half_day"] = updated["work_hours"] < 4.50
        else:
            updated["work_hours"] = None
            updated["overtime_minutes"] = 0
            updated["half_day"] = False

    fields = [
        "status",
        "punch_in",
        "punch_out",
        "work_hours",
        "late_minutes",
        "overtime_minutes",
        "half_day",
    ]
    changes = record_changes(old, updated, fields)
    if not changes:
        raise HTTPException(422, "Regularization made no changes")
    history = list(old.get("history", []))
    history.append({
        "at": now_ist().astimezone(UTC),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    })
    values = {field: updated.get(field) for field in fields}
    values["history"] = history

    if "history" in old:
        concurrency_filter = {
            "_id": old["_id"],
            "history": old["history"],
        }
    else:
        concurrency_filter = {
            "_id": old["_id"],
            "history": {"$exists": False},
        }

    result = db.attendance_logs.update_one(
        concurrency_filter,
        {"$set": values},
    )

    if result.modified_count != 1:
        raise HTTPException(409, "Attendance changed concurrently")
    return clean_doc(db.attendance_logs.find_one({"_id": old["_id"]}))


def month_log_match(emp_code, first, last, joined_on=None):
    match = {
        "emp_code": emp_code,
        "date": {"$gte": first.isoformat(), "$lte": last.isoformat()},
    }
    if joined_on:
        match["date"]["$gte"] = max(first, joined_on).isoformat()
    return match


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    employee = employee_or_404(emp_code)
    first, last = month_bounds(month)
    joined = parse_date(employee["joined_on"])
    start = max(first, joined)
    workdays = working_days(start, last) if start <= last else 0
    pipeline = [
        {"$match": month_log_match(emp_code, first, last, joined)},
        {"$set": {
            "_weekday": {
                "$dayOfWeek": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d",
                    }
                }
            },            "_overtime": {"$ifNull": ["$overtime_minutes", 0]},
            "_half": {"$ifNull": ["$half_day", False]},
        }},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": [
                    {"$in": ["$status", list(PRESENT_STATUSES)]},
                    {"$gte": ["$_weekday", 2]},
                    {"$lte": ["$_weekday", 6]},
                ]},
                {"$cond": ["$_half", 0.5, 1.0]},
                0,
            ]}},
            "leave_days": {"$sum": {"$cond": [
                {"$eq": ["$status", "LEAVE"]}, 1, 0
            ]}},
            "late_count": {"$sum": {"$cond": [
                {"$gt": ["$_late", 0]}, 1, 0
            ]}},
            "total_late_minutes": {"$sum": "$_late"},
            "total_overtime_minutes": {"$sum": "$_overtime"},
        }},
    ]
    results = list(db.attendance_logs.aggregate(pipeline))
    totals = results[0] if results else {}
    present = float(totals.get("present_days", 0))
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": workdays,
        "present_days": present,
        "leave_days": int(totals.get("leave_days", 0)),
        "late_count": int(totals.get("late_count", 0)),
        "total_late_minutes": int(totals.get("total_late_minutes", 0)),
        "total_overtime_minutes": int(
            totals.get("total_overtime_minutes", 0)
        ),
        "attendance_pct": (
            round_half_up(present / workdays * 100, 4) if workdays else None
        ),
    }


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    first, last = month_bounds(month)
    employee_match = {"joined_on": {"$lte": last.isoformat()}}
    if department:
        employee_match["department"] = department
    pipeline = [
        {"$match": employee_match},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code", "joined": "$joined_on"},
            "pipeline": [{"$match": {"$expr": {"$and": [
                {"$eq": ["$emp_code", "$$code"]},
                {"$gte": ["$date", first.isoformat()]},
                {"$lte": ["$date", last.isoformat()]},
                {"$gte": ["$date", "$$joined"]},
            ]}}}],
            "as": "logs",
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$set": {"_weekday": {"$cond": [
            {"$ne": ["$logs.date", None]},
            {"$dayOfWeek": {
                "$dateFromString": {"dateString": "$logs.date"}
            }},
            None,
        ]}}},
        {"$group": {
            "_id": "$department",
            "headcount_codes": {"$addToSet": "$emp_code"},
            "present_days": {"$sum": {"$cond": [
                {"$and": [
                    {"$in": ["$logs.status", list(PRESENT_STATUSES)]},
                    {"$gte": ["$_weekday", 2]},
                    {"$lte": ["$_weekday", 6]},
                ]},
                {"$cond": [
                    {"$eq": ["$logs.half_day", True]}, 0.5, 1.0
                ]},
                0,
            ]}},
            "hours": {"$push": {"$cond": [
                {"$and": [
                    {"$in": ["$logs.status", list(PRESENT_STATUSES)]},
                    {"$ne": ["$logs.work_hours", None]},
                ]},
                "$logs.work_hours",
                None,
            ]}},
            "late_count": {"$sum": {"$cond": [
                {"$gt": ["$logs.late_minutes", 0]}, 1, 0
            ]}},
            "total_late_minutes": {"$sum": {
                "$ifNull": ["$logs.late_minutes", 0]
            }},
            "leave_count": {"$sum": {"$cond": [
                {"$eq": ["$logs.status", "LEAVE"]}, 1, 0
            ]}},
            "on_duty_count": {"$sum": {"$cond": [
                {"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0
            ]}},
        }},
        {"$set": {
            "headcount": {"$size": "$headcount_codes"},
            "_hours": {"$filter": {
                "input": "$hours",
                "as": "h",
                "cond": {"$ne": ["$$h", None]},
            }},
        }},
        {"$set": {"avg_work_hours": {"$cond": [
            {"$gt": [{"$size": "$_hours"}, 0]},
            {"$round": [{"$avg": "$_hours"}, 2]},
            None,
        ]}}},
        {"$sort": {"_id": 1}},
        {"$project": {
            "_id": 0,
            "department": "$_id",
            "headcount": 1,
            "present_days": 1,
            "avg_work_hours": 1,
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1,
        }},
    ]
    return {"month": month, "items": list(db.employees.aggregate(pipeline))}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    first, last = month_bounds(month)
    pipeline = [
        {"$match": {
            "date": {"$gte": first.isoformat(), "$lte": last.isoformat()},
            "late_minutes": {"$gt": 0},
        }},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1},
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "employee",
        }},
        {"$unwind": "$employee"},
    ]
    if department:
        pipeline.append({"$match": {"employee.department": department}})
    pipeline.extend([
        {"$set": {
            "emp_code": "$_id",
            "name": "$employee.name",
            "department": "$employee.department",
        }},
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}},
        }},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {
            "_id": 0,
            "emp_code": 1,
            "name": 1,
            "department": 1,
            "total_late_minutes": 1,
            "late_count": 1,
            "rank": 1,
        }},
    ])
    return {"month": month, "items": list(db.attendance_logs.aggregate(pipeline))}


def explain_json(command):
    # Collectionless aggregations (those using aggregate=1, such as the
    # department trend pipeline that starts with $documents) must run against
    # the admin database. All collection-backed explain commands use the app DB.
    explained_command = command.get("explain", {})
    target_db = (
        client.admin
        if explained_command.get("aggregate") == 1
        else db
    )
    return json.loads(dumps(target_db.command(command)))


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
):
    start, end = parse_date(from_), parse_date(to)

    if end < start or (end - start).days + 1 > 92:
        raise HTTPException(422, "Invalid date range; maximum range is 92 days")

    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "Department not found")

    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end + timedelta(days=1), time.min)

    pipeline = [
        {"$documents": [{"_date": start_dt}]},
        {"$densify": {
            "field": "_date",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [start_dt, end_dt],
            },
        }},
        {"$set": {
            "_seed_date": {
                "$dateToString": {
                    "date": "$_date",
                    "format": "%Y-%m-%d",
                    "timezone": "UTC",
                }
            }
        }},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"day": "$_seed_date"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$date", "$$day"]}}},
                {"$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "employee",
                }},
                {"$unwind": "$employee"},
                {"$match": {"employee.department": department}},
                {"$project": {
                    "status": 1,
                    "late_minutes": 1,
                    "half_day": 1,
                }},
            ],
            "as": "_logs",
        }},
        {"$set": {
            "present_count": {"$sum": {"$map": {
                "input": {"$filter": {
                    "input": "$_logs",
                    "as": "l",
                    "cond": {"$in": ["$$l.status", list(PRESENT_STATUSES)]},
                }},
                "as": "l",
                "in": {"$cond": [
                    {"$eq": ["$$l.half_day", True]}, 0.5, 1.0
                ]},
            }}},
            "late_count": {"$size": {"$filter": {
                "input": "$_logs",
                "as": "l",
                "cond": {"$gt": ["$$l.late_minutes", 0]},
            }}},
            "is_working_day": {"$and": [
                {"$gte": [{"$dayOfWeek": "$_date"}, 2]},
                {"$lte": [{"$dayOfWeek": "$_date"}, 6]},
            ]},
        }},
        {"$lookup": {
            "from": "employees",
            "let": {"day": "$_seed_date"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$department", department]},
                    {"$lte": ["$joined_on", "$$day"]},
                ]}}},
                {"$count": "count"},
            ],
            "as": "_hc",
        }},
        {"$set": {
            "headcount": {
                "$ifNull": [{"$arrayElemAt": ["$_hc.count", 0]}, 0]
            },
        }},
        {"$set": {
            "attendance_rate": {"$cond": [
                {"$and": [
                    "$is_working_day",
                    {"$gt": ["$headcount", 0]},
                ]},
                {"$round": [
                    {"$divide": ["$present_count", "$headcount"]}, 4
                ]},
                None,
            ]},
        }},
        {"$setWindowFields": {
            "sortBy": {"_date": 1},
            "output": {
                "moving_avg_7d": {
                    "$avg": "$attendance_rate",
                    "window": {"documents": [-6, 0]},
                },
            },
        }},
        {"$set": {"date": "$_seed_date"}},
        {"$project": {
            "_id": 0,
            "date": 1,
            "present_count": 1,
            "late_count": 1,
            "headcount": 1,
            "is_working_day": 1,
            "attendance_rate": 1,
            "moving_avg_7d": 1,
        }},
        {"$sort": {"date": 1}},
    ]

    # $documents is a database-level aggregation stage.
    items = list(db.aggregate(pipeline))

    for item in items:
        if item.get("moving_avg_7d") is not None:
            item["moving_avg_7d"] = round_half_up(item["moving_avg_7d"], 4)
        if item.get("attendance_rate") is not None:
            item["attendance_rate"] = round_half_up(item["attendance_rate"], 4)

    return {"department": department, "items": items}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if endpoint == "attendance_list":
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if date_from or date_to:
            date_filter = {}
            if date_from:
                parse_date(date_from)
                date_filter["$gte"] = date_from
            if date_to:
                parse_date(date_to)
                date_filter["$lte"] = date_to
            if date_from and date_to and date_from > date_to:
                raise HTTPException(422, "date_from must not exceed date_to")
            query["date"] = date_filter
        if status:
            query["status"] = validate_status(status)
        command = {
            "explain": {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            "verbosity": "executionStats",
        }
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_json(command),
        }

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")
        employee = employee_or_404(emp_code)
        first, last = month_bounds(month)
        joined = parse_date(employee["joined_on"])
        pipeline = [
            {"$match": month_log_match(emp_code, first, last, joined)},
            {"$set": {
                "_weekday": {"$dayOfWeek": {
                    "$dateFromString": {"dateString": "$date"}
                }},
            }},
            {"$group": {"_id": "$emp_code", "count": {"$sum": 1}}},
        ]
        collection = "attendance_logs"

    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")
        first, last = month_bounds(month)
        match = {"joined_on": {"$lte": last.isoformat()}}
        if department:
            match["department"] = department
        pipeline = [
            {"$match": match},
            {"$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code", "joined": "$joined_on"},
                "pipeline": [{"$match": {"$expr": {"$and": [
                    {"$eq": ["$emp_code", "$$code"]},
                    {"$gte": ["$date", first.isoformat()]},
                    {"$lte": ["$date", last.isoformat()]},
                    {"$gte": ["$date", "$$joined"]},
                ]}}}],
                "as": "logs",
            }},
        ]
        collection = "employees"

    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month is required")
        first, last = month_bounds(month)
        pipeline = [
            {"$match": {
                "date": {"$gte": first.isoformat(), "$lte": last.isoformat()},
                "late_minutes": {"$gt": 0},
            }},
            {"$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }},
            {"$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }},
            {"$unwind": "$employee"},
        ]
        if department:
            pipeline.append({"$match": {"employee.department": department}})
        pipeline.extend([
            {"$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {"rank": {"$rank": {}}},
            }},
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {"$match": {"rank": {"$lte": limit}}},
        ])
        collection = "attendance_logs"

    elif endpoint == "department_trend":
        if not department or not from_ or not to:
            raise HTTPException(422, "department, from, and to are required")

        start, end = parse_date(from_), parse_date(to)
        if end < start or (end - start).days + 1 > 92:
            raise HTTPException(422, "Invalid date range")

        if not db.employees.find_one({"department": department}):
            raise HTTPException(404, "Department not found")

        start_dt = datetime.combine(start, time.min)
        end_dt = datetime.combine(end + timedelta(days=1), time.min)

        # Explain the indexed attendance-log access that feeds the department
        # trend. The configured MongoDB-compatible server rejects aggregate=1,
        # so this explain must be collection-backed (attendance_logs).
        explain_pipeline = [
            {"$match": {
                "date": {
                    "$gte": start.isoformat(),
                    "$lte": end.isoformat(),
                },
            }},
            {"$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }},
            {"$unwind": "$employee"},
            {"$match": {"employee.department": department}},
            {"$project": {
                "_id": 0,
                "date": 1,
                "emp_code": 1,
                "status": 1,
                "late_minutes": 1,
                "half_day": 1,
            }},
        ]

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain_json({
                "explain": {
                    "aggregate": "attendance_logs",
                    "pipeline": explain_pipeline,
                    "cursor": {},
                },
                "verbosity": "executionStats",
            }),
        }

    else:
        raise HTTPException(422, "Unknown endpoint")

    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": explain_json({
            "explain": {
                "aggregate": collection,
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        }),
    }