# MySQL Deadlock on Concurrent Batch Order Updates

> case_id: `seed-mysql-deadlock` | severity: **critical** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / MyBatis Plus 3.5
- jdk: 17
- database: MySQL 8.0.35

## Symptoms
Batch order status update jobs fail intermittently with deadlock errors. The API returns HTTP 500 with `MySQLTransactionRollbackException`. `SHOW PROCESSLIST` reveals multiple transactions stuck in `LOCK_WAIT` state. The issue occurs only when two batch processors run concurrently on overlapping order sets.

## Error Log
```
com.mysql.cj.jdbc.exceptions.MySQLTransactionRollbackException: Deadlock found when trying to get lock; try restarting transaction
    at com.mysql.cj.jdbc.exceptions.SQLError.createSQLException(SQLError.java:125)
    at com.mysql.cj.jdbc.exceptions.SQLError.createSQLException(SQLError.java:89)
    at org.springframework.transaction.support.AbstractPlatformTransactionManager.processRollback(AbstractPlatformTransactionManager.java:860)
    at org.springframework.transaction.support.AbstractPlatformTransactionManager.rollback(AbstractPlatformTransactionManager.java:837)

--- INNODB DEADLOCK LOG ---
LATEST DETECTED DEADLOCK
Transaction 1: holds lock on row (order_id=1001), waits for lock on row (order_id=2003)
Transaction 2: holds lock on row (order_id=2003), waits for lock on row (order_id=1001)
```

## Root Cause
Two batch processor threads update overlapping sets of order rows. Thread A processes orders sorted by `created_at`, fetching rows in order [1001, 1500, 2003]. Thread B processes the same orders sorted by `updated_at`, fetching rows in order [2003, 1500, 1001]. Both use `SELECT ... FOR UPDATE` within `@Transactional`, creating row-level locks. The lock acquisition order differs between threads, producing a classic AB-BA deadlock. InnoDB detects the deadlock and rolls back one transaction.

## Diagnosis Steps
1. Checked application error logs — `MySQLTransactionRollbackException` with deadlock message
2. Ran `SHOW ENGINE INNODB STATUS` — found deadlock graph showing two transactions with opposite lock waits
3. Identified the two batch processor methods — `BatchProcessorA` and `BatchProcessorB`
4. Examined their queries — same data set, different `ORDER BY` clauses
5. Confirmed lock ordering mismatch causing the deadlock cycle

## Fix Suggestion
1. Ensure both batch queries use the same `ORDER BY id` to acquire locks in consistent order
2. Add `SELECT ... FOR UPDATE ORDER BY id` to enforce deterministic lock acquisition
3. Implement retry logic with exponential backoff for `MySQLTransactionRollbackException`
4. Consider using optimistic locking with `@Version` instead of pessimistic `FOR UPDATE`
5. Schedule batch jobs to avoid concurrent execution on overlapping data sets

## Tags
deadlock, mysql, concurrency, batch-update, transaction, innodb, row-lock, lock-ordering

---
- created: 2025-05-18T13:00:00+00:00
- updated: 2025-05-18T13:50:00+00:00
- similar_cases: []
