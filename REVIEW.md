# REVIEW.md

## Review of the attendance API starter

| # | Location | Defect identified | How to notice it | Correction |
|---|---|---|---|---|
| 1 | `parse_date` | Parsing a date without enforcing the contract's exact `YYYY-MM-DD` format can accept unintended representations. | Submit a date such as `2026-7-1` or an impossible calendar date. | Validate the exact format and then validate the calendar date; invalid input returns HTTP 422. |
| 2 | `epoch_ms`, `as_ist`, `clean_doc` | MongoDB datetimes and API timestamps must be converted consistently, with naive MongoDB datetimes interpreted as UTC. Internal MongoDB identifiers must not leak into API responses. | Inspect timestamp types in employee, attendance, and history responses. | Normalize datetimes and serialize instants as epoch milliseconds; omit internal `_id` and `id` fields. |
| 3 | `EmployeeIn` | Input validation needs to reject malformed email addresses and invalid shift/date values. A normal model method is not a Pydantic model validator. | Submit an invalid email, malformed date, invalid shift, or equal shift start/end values. | Add explicit field validation and a model-level check that shift start and end differ. |
| 4 | `/health` | Returning success without contacting MongoDB can report a healthy service while the database is unavailable. | Stop or make the database unavailable and request `/health`. | Ping MongoDB and return HTTP 503 when the database cannot be reached. |
| 5 | `regularize_attendance` | Concurrent corrections must not overwrite one another's audit history silently. | Submit two corrections concurrently for the same attendance record. | Use a conditional update based on the previously read history and return HTTP 409 when another update wins. |

## Behavior reviewed and retained

- `compute_late_minutes` applies the strict 10-minute grace boundary and reports whole elapsed minutes.
- `compute_work_hours` uses decimal half-up rounding to two decimal places.
- `compute_overtime` applies the 30-minute minimum and accounts for overnight shifts when shift information is supplied.
- Unique database indexes protect employee codes and the employee/date attendance key.

## Verification notes

Terminal smoke tests returned HTTP 200 for the main API and all five explain endpoints. The department trend returned seven consecutive daily rows for the tested date range, and the tested explain plans contained `IXSCAN` without `COLLSCAN`. Concurrent punch-ins, punch-outs, and regularization produced the expected one-success/one-conflict behavior. These checks do not replace testing against the full hidden dataset.
