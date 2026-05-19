# Database Connection Leak in Manual JDBC Query Code

> case_id: `seed-db-conn-leak` | severity: **critical** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / JDBC
- jdk: 17
- database: PostgreSQL 15

## Symptoms
The application gradually loses ability to query the database. HikariCP logs show `active` connections steadily increasing until the pool maximum is reached, after which all new queries fail with connection timeout. The connections are never returned to the pool. Restarting the application temporarily fixes the issue.

## Error Log
```
java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.
    at com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:695)
    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:196)

HikariPool-1 stats over time:
  10:00 - total=10, active=2,  idle=8,  waiting=0
  12:00 - total=10, active=7,  idle=3,  waiting=0
  14:00 - total=10, active=10, idle=0,  waiting=15
  14:05 - total=10, active=10, idle=0,  waiting=47  (connection timeout)

Hikari leak detection:
  Connection leak detection threshold: 60000ms
  Recent leaks: 8 connections held for > 60s
  Stack trace of leaked connection acquisition:
    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:186)
    at cn.metast.lingxi.module.report.CustomReportService.executeRawQuery(CustomReportService.java:45)
```

## Root Cause
`CustomReportService.executeRawQuery()` acquires a JDBC `Connection` from `DataSource.getConnection()` but does not close it in a `finally` block. When the query throws a `SQLException`, the connection is never returned to the HikariCP pool. Each error leaks one connection. Over hours, leaked connections accumulate until the pool is exhausted. The code uses manual JDBC instead of Spring's `JdbcTemplate`, bypassing automatic resource management.

## Diagnosis Steps
1. Monitored HikariCP metrics — active connections steadily increasing
2. Enabled `leak-detection-threshold=60000` — got stack traces pointing to `CustomReportService.java:45`
3. Examined `executeRawQuery()` — `Connection` obtained but no `finally` block to close it
4. Confirmed: `ResultSet`, `Statement`, and `Connection` not closed on exception path
5. Counted leaked connections — 8 active connections all from the same method

## Fix Suggestion
1. Use try-with-resources: `try (Connection conn = dataSource.getConnection(); Statement stmt = conn.createStatement()) { ... }`
2. Replace manual JDBC with `JdbcTemplate` which handles connection lifecycle automatically
3. Set `hikari.leak-detection-threshold=30000` for early detection
4. Add connection pool metrics monitoring with alert on `active > 80% of max`
5. Code review all raw JDBC usage in the codebase for similar leaks

## Tags
connection-leak, jdbc, hikaricp, resource-management, try-with-resources, database, postgresql

---
- created: 2025-05-18T15:30:00+00:00
- updated: 2025-05-18T16:15:00+00:00
- similar_cases: []
