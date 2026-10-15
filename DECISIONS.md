# DECISIONS.md

1. **Indexes.** A unique index on `employees.emp_code` prevents duplicate employee codes. A unique compound index on `attendance_logs.emp_code` and `date` prevents multiple records for one employee on the same attendance date. An index on `date` and `emp_code` supports date-based attendance queries. An index on `status` and `date` supports status/date filters. Additional indexes should be justified by query plans and measured workloads.

2. **Punch-in race.** Two requests can both check for an existing record before either inserts. The unique compound index is the final protection: one insert succeeds, and the other must catch the duplicate-key error and return HTTP 409. This depends on the endpoint handling that error correctly.

3. **Ties.** The leaderboard sorts by late minutes descending and employee code ascending. Employees tied on late minutes receive the same competition rank. If a tie crosses the limit, all employees tied at that cutoff are included.

4. **Headcount.** Headcount is based on employees whose joining date is on or before the period end, not just employees with attendance logs. Employees with zero logs can therefore still count.

5. **Scaling.** For 100 times the data, I would move analytics calculations into indexed MongoDB aggregation pipelines and use execution statistics to identify bottlenecks.
