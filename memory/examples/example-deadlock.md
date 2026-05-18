# Deadlock on Concurrent Order Status Update

> case_id: `example003` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2
- jdk: 17
- database: MySQL 8.0
- module: order-service

## Symptoms
API requests to update order status hang indefinitely (timeout after 30s). MySQL `SHOW PROCESSLIST` shows multiple transactions in `LOCK_WAIT` state. Occurs only during high-concurrency order batch processing.

## Error Log
```
com.mysql.cj.jdbc.exceptions.MySQLTransactionRollbackException: Deadlock found when trying to get lock; try restarting transaction
    at com.mysql.cj.jdbc.exceptions.SQLError.createSQLException(SQLError.java:125)
    at org.springframework.transaction.support.AbstractPlatformTransactionManager.processRollback(AbstractPlatformTransactionManager.java:860)
```

## Root Cause
Two batch threads update orders in different sort orders. Thread A updates orders [1, 2, 3] sequentially, Thread B updates orders [3, 2, 1]. This creates a classic deadlock: A holds lock on 1, waits for 3; B holds lock on 3, waits for 1.

The `@Transactional` method `updateOrderStatus()` acquires row-level locks in the order the rows are fetched, which differs between batches.

## Diagnosis Steps
1. Checked timeout errors in application logs
2. Ran `SHOW ENGINE INNODB STATUS` → found deadlock graph
3. Identified two transactions holding and waiting for opposite row locks
4. Traced to `OrderBatchProcessor` which processes orders without sorting
5. Confirmed deadlock pattern: lock ordering mismatch

## Fix Suggestion
1. Sort order IDs before batch processing to ensure consistent lock acquisition order
2. Add `ORDER BY id` to the batch query
3. Use `SELECT ... FOR UPDATE` with consistent ordering
4. Consider optimistic locking with `@Version` as an alternative
5. Add retry logic for deadlock exceptions with exponential backoff

## Tags
deadlock, mysql, concurrency, spring-boot, transaction, batch-processing, locking, order-by

---
- created: 2025-05-18T16:00:00+00:00
- updated: 2025-05-18T16:45:00+00:00
- similar_cases: []
