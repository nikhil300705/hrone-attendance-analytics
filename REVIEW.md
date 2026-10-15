# REVIEW.md

## Review of the attendance API

| # | Area | Defect or risk | How to test | Required correction |
|---|---|---|---|---|
| 1 | Late-minute calculation | The strict 10-minute grace boundary must be handled correctly, while reported late minutes represent full elapsed minutes. | For a 09:30 shift, test 09:40:00, 09:40:01, and 10:15:59. | Count lateness only when elapsed time is strictly greater than 10 minutes, and floor elapsed minutes. |
| 2 | Work-hour rounding | Python's built-in round uses ties-to-even, not the required decimal half-up rule. | Test a duration whose hours value is halfway between two hundredths. | Round elapsed seconds divided by 3600 to two decimals using half-up rounding. |
| 3 | Overtime calculation | The 30-minute minimum and overnight shift-end handling must both be enforced. | Test overtime below 30 minutes, exactly 30 minutes, and an overnight shift. | Resolve the correct shift-end instant and count only whole overtime minutes when the duration is at least 30 minutes. |
| 4 | Request validation | Employee fields, timestamps, and attendance statuses must follow the API contract. | Try malformed employee codes, invalid email addresses, unsupported statuses, and invalid epoch values. | Validate formats, ranges, allowed values, and equal shift start/end times. |
| 5 | Duplicate employee creation | A pre-insert existence check alone cannot prevent concurrent duplicate requests. | Send concurrent requests with the same employee code. | Enforce a unique database index and translate duplicate-key errors to HTTP 409. |
| 6 | Attendance operations | Duplicate records, invalid punch ordering, 24-hour limits, and IST dates for overnight shifts require strict validation. | Test duplicate punch-ins, punch-out before punch-in, and overnight shifts. | Apply the contract rules and enforce attendance uniqueness in MongoDB. |
| 7 | Analytics | Sample responses do not prove correct aggregation, rounding, join-date handling, or inclusion of employees without logs. | Test weekends, mid-month joining dates, half-days, ties, and employees with no logs. | Verify every calculation against the assignment requirements and OpenAPI contract. |
| 8 | Department trend and explain plans | The required MongoDB `$densify` stage and representative execution-plan checks must be verified. | Inspect the trend pipeline and run explain with execution statistics. | Use the required aggregation stages and check index usage on the actual query shapes. |

## Verification status

This review identifies important contract requirements and risks. Each finding must be confirmed against the original starter code and the assignment specification before it is considered a verified original defect. Basic endpoint smoke tests alone do not establish full correctness or performance compliance.
