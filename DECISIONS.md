# DECISIONS.md

1. **Indexes.** A unique index on `employees.emp_code` prevents duplicate employee codes. A unique compound index on `attendance_logs.emp_code` and `date` guarantees one attendance record per employee per attendance day. The `date`/`emp_code` index supports date-filtered attendance queries, while the `status`/`date` index supports status-and-date filters. The department/join-date index supports headcount queries.

2. **Punch-in race.** Concurrent requests may both attempt to insert the same attendance key. The unique database index allows one insert; the other raises a duplicate-key error that the endpoint converts to HTTP 409.

3. **Ties.** The leaderboard uses competition ranking, so employees with equal late-minute totals share a rank. The cutoff is applied after ranking, so every employee tied at the cutoff is returned.

4. **Headcount.** Department headcount comes from employees who joined on or before the period end, independently of their attendance logs. Employees with no logs are therefore still counted.

5. **Scaling.** For 100 times the data, I would profile execution statistics on realistic workloads, tune compound indexes to match common filters and sorts, and consider precomputed monthly summaries if measured analytics latency justified the added maintenance cost.
