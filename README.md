# Employee Attendance & Analytics API

A REST API built with **Python, FastAPI, and MongoDB** for employee attendance management, punch-in/punch-out workflows, attendance regularization, and workforce analytics.

The project emphasizes server-side validation, consistent attendance calculations, auditable manual corrections, and aggregation-based analytics.

## Features

- **Employee management:** Create employees, enforce unique employee codes, and retrieve paginated employee lists with optional department filtering.
- **Attendance tracking:** Record punch-in and punch-out events with timestamp validation, overnight-shift handling, late arrival calculations, overtime, and half-day detection.
- **Attendance regularization:** Correct attendance records while preserving an audit history of changes, the person making the correction, and the reason.
- **Employee analytics:** Calculate monthly working days, present days, leave days, late arrivals, overtime, and attendance percentage.
- **Department analytics:** Summarize headcount, attendance, working hours, late arrivals, leave, and on-duty records.
- **Late-arrival leaderboard:** Rank employees by total late minutes, supporting department filters and ranking ties.
- **Department trends:** Generate daily attendance trends, including dates without attendance records, using MongoDB aggregation and `$densify`.
- **Query-plan inspection:** Expose explain endpoints for inspecting aggregation plans and index usage.

## Technology Stack

| Component | Technology |
|---|---|
| API framework | FastAPI |
| Language | Python 3.11+ |
| Database | MongoDB 6.0+ |
| Database driver | PyMongo |
| Data validation | Pydantic |
| API documentation | OpenAPI / Swagger UI |

## Project Structure

```text
candidate_kit/
├── app/
│   ├── __init__.py
│   └── main.py
├── sample_data/
│   ├── employees.json
│   └── attendance_logs.json
├── .gitignore
├── DATA_MODEL.md
├── DECISIONS.md
├── REVIEW.md
├── openapi.yaml
├── requirements.txt
└── sample_seed.py
```

## Local Setup

### Prerequisites

- Python 3.11 or newer
- MongoDB 6.0 or newer
- Git

### 1. Clone the repository

```bash
git clone https://github.com/nikhil300705/hrone-attendance-analytics.git
cd hrone-attendance-analytics
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
python3 -m pip install -r requirements.txt
```

### 4. Configure MongoDB

Create a local `.env` file in the project root:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Replace the URI with your MongoDB Atlas connection string when using Atlas. Keep credentials in `.env`; never commit this file.

Ensure MongoDB is running and reachable before starting the API.

### 5. Start the API

```bash
uvicorn app.main:app --reload --port 8000
```

Verify the health endpoint:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status": "ok"}
```

To load the supplied sample fixtures, inspect `sample_seed.py` first and run it only against a disposable development database, since it reloads sample data.

## API Endpoints

The interactive Swagger UI is available at `http://127.0.0.1:8000/docs` while the server is running.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Check API and database connectivity |
| `POST` | `/employees` | Create an employee |
| `GET` | `/employees` | List employees with pagination and department filtering |
| `POST` | `/attendance/punch-in` | Record an employee punch-in |
| `POST` | `/attendance/punch-out` | Record a punch-out and calculate working time |
| `GET` | `/attendance` | Query attendance with filters and pagination |
| `PATCH` | `/attendance/{emp_code}/{date_str}` | Regularize an attendance record |
| `GET` | `/analytics/employees/{emp_code}/monthly` | Retrieve monthly employee analytics |
| `GET` | `/analytics/departments/summary` | Retrieve department-level monthly summaries |
| `GET` | `/analytics/leaderboard/late` | Retrieve the late-arrival leaderboard |
| `GET` | `/analytics/departments/{department}/trend` | Retrieve daily department attendance trends |
| `GET` | `/admin/explain/{endpoint}` | Inspect query or aggregation execution plans |

See `openapi.yaml` for the API contract and request/response specifications.

## Business Rules and Design

- Attendance dates are calculated using **Indian Standard Time (IST)** and account for overnight shifts.
- Punch timestamps are accepted as epoch milliseconds and normalized to whole seconds.
- Late arrival, overtime, working hours, and half-day status follow the assignment's attendance rules.
- Working-day calculations use Monday through Friday, without a holiday calendar.
- Manual corrections record an audit history and reject invalid or unchanged updates.
- Analytics are calculated with MongoDB aggregation pipelines rather than loading full datasets into application memory.
- Database indexes support employee lookups, attendance-date queries, and department-based filtering.

The data model and implementation decisions are documented in [`DATA_MODEL.md`](DATA_MODEL.md) and [`DECISIONS.md`](DECISIONS.md). The code review is available in [`REVIEW.md`](REVIEW.md).

## Configuration and Security

- Do not commit `.env` files, database credentials, access tokens, or other secrets.
- Configure database access through environment variables.
- Keep the repository public for the period required by the assignment.

## Project Status

This repository was created as a technical assignment for the **Software Engineer Trainee** role at HROne. The API has been smoke-tested against the configured MongoDB database; comprehensive automated testing of all business-rule edge cases remains a separate verification step.

## Known limitation

The department-trend explain endpoint currently explains the indexed attendance-log query feeding the trend, rather than the complete gap-filling trend pipeline. Basic smoke tests pass, but full hidden-dataset correctness has not been exhaustively verified.
