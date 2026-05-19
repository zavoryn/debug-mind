# Redis Connection Pool Exhaustion Causing NPE in Session Service

> case_id: `seed-redis-pool` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / Lettuce 6.3
- jdk: 17
- module: session-service

## Symptoms
The `/api/session/validate` endpoint intermittently returns HTTP 500 during peak traffic. Stack trace shows NullPointerException where a Redis-fetched session token is used without null checking. Monitoring shows Redis connection pool utilization hitting 100%.

## Error Log
```
java.lang.NullPointerException: Cannot invoke "String.equals(Object)" because the return value of "cn.metast.lingxi.module.session.SessionService.getSessionToken()" is null
    at cn.metast.lingxi.module.session.SessionService.validateSession(SessionService.java:87)
    at cn.metast.lingxi.module.session.controller.SessionController.validate(SessionController.java:34)
    at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
Caused by: io.lettuce.core.RedisException: Connection pool exhausted
    at io.lettuce.core.pool.ConnectionPool.acquire(ConnectionPool.java:156)
    at io.lettuce.core.pool.ConnectionPool.acquire(ConnectionPool.java:132)
```

## Root Cause
Redis connection pool was configured with `max-active=8` and `max-idle=4`, which is insufficient for peak traffic exceeding 200 concurrent requests. When the Lettuce connection pool is exhausted, `getSessionToken()` catches the `RedisException` and returns null instead of propagating the error. The calling code at line 87 performs `.equals()` on this null value, causing the NPE. The combination of connection pool exhaustion and missing null check creates the intermittent failure.

## Diagnosis Steps
1. Located NPE stack trace pointing to SessionService.java:87
2. Traced `getSessionToken()` method — delegates to Lettuce Redis connection pool
3. Examined Redis pool configuration — `lettuce.pool.max-active=8`
4. Correlated failure timestamps with Grafana metrics showing pool utilization at 100%
5. Confirmed root cause: pool exhaustion causes null return, downstream code lacks null check

## Fix Suggestion
1. Increase Redis connection pool: `lettuce.pool.max-active=32`, `lettuce.pool.max-idle=16`, `lettuce.pool.min-idle=8`
2. Add null check in `validateSession()` before calling `.equals()` on the token
3. Throw a descriptive `SessionUnavailableException` instead of returning null
4. Add circuit breaker (Resilience4j) for Redis failures
5. Set up monitoring alert on `lettuce.pool.active` utilization exceeding 80%

## Tags
npe, redis, connection-pool, lettuce, spring-boot, null-check, pool-exhaustion, session

---
- created: 2025-05-18T10:30:00+00:00
- updated: 2025-05-18T11:15:00+00:00
- similar_cases: []
