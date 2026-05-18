# NPE in UserService.login when Redis connection pool exhausted

> case_id: `example001` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2
- jdk: 17
- module: user-service

## Symptoms
Login endpoint returns HTTP 500 intermittently. Stack trace shows NullPointerException at UserService.java:42. Issue occurs during peak traffic hours.

## Error Log
```
java.lang.NullPointerException: Cannot invoke "String.equals(Object)" because the return value of "cn.metast.lingxi.module.user.UserService.getLoginToken()" is null
    at cn.metast.lingxi.module.user.userService.login(UserService.java:42)
    at cn.metast.lingxi.module.user.controller.UserController.login(UserController.java:28)
    at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
Caused by: io.lettuce.core.RedisException: Connection pool exhausted
    at io.lettuce.core.pool.ConnectionPool.acquire(ConnectionPool.java:156)
```

## Root Cause
Redis connection pool configured with max-active=8 which is insufficient for peak traffic. When pool is exhausted, getLoginToken() returns null instead of throwing, and downstream code performs .equals() on null.

## Diagnosis Steps
1. Located NPE stack trace → UserService.java:42
2. Traced getLoginToken() → calls Redis for session token
3. Checked Redis connection pool config → max-active=8
4. Correlated with traffic pattern → peak hours exceed 8 concurrent connections
5. Confirmed root cause: pool exhaustion + missing null check

## Fix Suggestion
1. Increase Redis connection pool: max-active=32, max-idle=16
2. Add null check in UserService.login() before .equals() call
3. Add circuit breaker pattern for Redis failures
4. Add monitoring alert for connection pool utilization

## Tags
npe, redis, connection-pool, spring-boot, lettuce, login, null-check, peak-traffic

---
- created: 2025-05-18T10:30:00
- updated: 2025-05-18T11:15:00
- similar_cases: []
