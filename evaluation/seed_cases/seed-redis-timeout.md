# Redis Command Timeout with Lettuce Client Under High Load

> case_id: `seed-redis-timeout` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / Lettuce 6.3
- jdk: 17
- middleware: Redis 7.2 (single node)

## Symptoms
Redis commands fail intermittently with `RedisCommandTimeoutException` under high load. The timeout is configured at 3 seconds, but actual Redis response times spike to 5+ seconds during traffic peaks. Application throughput drops as threads block waiting for Redis responses. The Redis server itself shows high `connected_clients` and slow command execution.

## Error Log
```
io.lettuce.core.RedisCommandTimeoutException: Command timed out after 3 second(s)
    at io.lettuce.core.LettuceFutures.awaitOrCancel(LettuceFutures.java:125)
    at io.lettuce.core.FutureSyncInvocationHandler.handleInvocation(FutureSyncInvocationHandler.java:69)
    at io.lettuce.core.internal.AsyncConnection.close(AsyncConnection.java:253)

Redis server stats during incident:
  connected_clients: 1024
  instantaneous_ops_per_sec: 85000
  used_memory: 3.8gb
  slowlog: KEYS * commands taking 4-8 seconds
```

## Root Cause
The application issues `KEYS *` commands in a cache management endpoint to list all cached keys. On a Redis instance with 500K+ keys, `KEYS *` blocks the single-threaded Redis server for 4-8 seconds. During this time, all other Redis commands queue up and exceed the 3-second Lettuce command timeout. The `KEYS` command is O(N) and should never be used in production with large key spaces. This was a debugging endpoint that was accidentally left enabled in production.

## Diagnosis Steps
1. Checked application logs — `RedisCommandTimeoutException` with 3s timeout
2. Connected to Redis and ran `INFO clients` — 1024 connected clients
3. Checked `SLOWLOG` — found `KEYS *` commands taking 4-8 seconds
4. Traced `KEYS *` to `CacheManagementController.listAllKeys()`
5. Confirmed: debugging endpoint enabled in production, blocking Redis server

## Fix Suggestion
1. Replace `KEYS *` with `SCAN` for iterative, non-blocking key enumeration
2. Disable or protect the cache management endpoint in production with `@Profile("!prod")`
3. Increase Lettuce command timeout to 5s as a safety margin
4. Configure Redis `maxclients` to prevent connection overload
5. Add Redis slow log monitoring alert for commands exceeding 1 second
6. Consider using a separate Redis instance for management/diagnostic commands

## Tags
redis, timeout, lettuce, keys-command, slowlog, performance, spring-boot, scan

---
- created: 2025-05-18T10:00:00+00:00
- updated: 2025-05-18T10:40:00+00:00
- similar_cases: []
