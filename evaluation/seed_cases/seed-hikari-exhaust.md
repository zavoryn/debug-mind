# HikariCP Connection Pool Exhaustion Under Load

> case_id: `seed-hikari-exhaust` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / HikariCP 5.1
- jdk: 17
- database: PostgreSQL 15

## Symptoms
REST API endpoints return HTTP 500 with "Unable to acquire JDBC connection" errors during traffic spikes. Response times degrade from 50ms to 30s before failing. HikariCP pool logs show all connections are active with a queue of waiting threads. After traffic subsides, connections are not released — they remain active indefinitely.

## Error Log
```
java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 30000ms.
    at com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:695)
    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:196)
    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:149)
    at com.zaxxer.hikari.HikariDataSource.getConnection(HikariDataSource.java:99)
    at org.springframework.jdbc.datasource.DataSourceTransactionManager.doBegin(DataSourceTransactionManager.java:341)

HikariPool-1 stats: total=10, active=10, idle=0, waiting=47, maxLifetime=1800000ms
```

## Root Cause
HikariCP was configured with `maximum-pool-size=10` for a service handling 200+ concurrent requests. Additionally, a code path in `ReportService.generateReport()` acquires a connection via `@Transactional` but performs a long-running PDF generation inside the transaction boundary. The PDF generation takes 15-30 seconds, holding the connection the entire time. Under load, all 10 connections are occupied by report generation, starving other endpoints. The `connection-timeout` is 30s, causing the timeout exception.

## Diagnosis Steps
1. Checked application logs — `SQLTransientConnectionException` with 30s timeout
2. Examined HikariCP metrics — `active=10, idle=0, waiting=47`
3. Profiled thread dumps — 8 of 10 connections held by `ReportService.generateReport()`
4. Traced report generation — PDF rendering happens inside `@Transactional` method
5. Confirmed: long-running non-DB work holds connections unnecessarily

## Fix Suggestion
1. Move PDF generation outside the `@Transactional` boundary — fetch data first, then generate PDF
2. Increase `maximum-pool-size` to 25 (or calculate based on formula: connections = core_count * 2 + disk_spindles)
3. Set `leak-detection-threshold=60000` to detect connection leaks
4. Add HikariCP metrics to Prometheus/Grafana for pool monitoring
5. Consider separate connection pools for report generation vs. API requests

## Tags
hikaricp, connection-pool, pool-exhaustion, spring-boot, transaction, timeout, jdbc, postgresql

---
- created: 2025-05-18T08:00:00+00:00
- updated: 2025-05-18T08:50:00+00:00
- similar_cases: []
