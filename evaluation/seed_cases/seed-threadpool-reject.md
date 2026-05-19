# Thread Pool Task Rejection Under Burst Load

> case_id: `seed-threadpool-reject` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Java ExecutorService / Spring Boot 3.2
- jdk: 17

## Symptoms
The notification service drops tasks during traffic bursts, with `RejectedExecutionException` appearing in logs. Users report not receiving push notifications. The error occurs when the thread pool's task queue is full and all core threads are busy. The default `AbortPolicy` rejects new tasks by throwing an exception.

## Error Log
```
java.util.concurrent.RejectedExecutionException: Task java.util.concurrent.FutureTask@5a3b7f8e rejected from java.util.concurrent.ThreadPoolExecutor@2c3f48e1[Running, pool size = 8, active threads = 8, queued tasks = 100, completed tasks = 45231]
    at java.base/java.util.concurrent.ThreadPoolExecutor$AbortPolicy.rejectedExecution(ThreadPoolExecutor.java:2065)
    at java.base/java.util.concurrent.ThreadPoolExecutor.reject(ThreadPoolExecutor.java:833)
    at java.base/java.util.concurrent.ThreadPoolExecutor.execute(ThreadPoolExecutor.java:1364)
    at cn.metast.lingxi.module.notification.NotificationService.sendAsync(NotificationService.java:56)
    at cn.metast.lingxi.module.notification.controller.NotificationController.sendBatch(NotificationController.java:38)
```

## Root Cause
The notification service uses a `ThreadPoolExecutor` configured with `corePoolSize=8`, `maxPoolSize=8`, and a `LinkedBlockingQueue` with capacity 100. When a burst of 200+ notification requests arrives simultaneously, all 8 threads are occupied and the queue fills to capacity. Since `corePoolSize == maxPoolSize`, no additional threads are created beyond 8. The default `AbortPolicy` throws `RejectedExecutionException`, causing notification loss. The root issue is that `maxPoolSize` was set equal to `corePoolSize`, preventing thread pool growth.

## Diagnosis Steps
1. Checked logs for `RejectedExecutionException` — confirmed at `NotificationService.java:56`
2. Examined thread pool configuration — `corePoolSize=8, maxPoolSize=8, queueCapacity=100`
3. Analyzed traffic pattern — burst of 200+ notifications during promotional campaign
4. Confirmed: 8 threads + 100 queue = max 108 tasks, excess tasks rejected
5. Identified: `maxPoolSize == corePoolSize` prevents pool from growing

## Fix Suggestion
1. Increase `maxPoolSize` to 32 to allow pool growth under burst load
2. Increase queue capacity from 100 to 500 for larger buffer
3. Use `CallerRunsPolicy` instead of `AbortPolicy` to prevent task loss
4. Consider using `SynchronousQueue` with `maxPoolSize=32` for burst-friendly behavior
5. Add monitoring for `ThreadPoolExecutor` metrics: active count, queue size, rejection count
6. Implement backpressure mechanism — return HTTP 429 when queue is 80% full

## Tags
threadpool, rejected-execution, executor, queue-full, burst-load, concurrency, java

---
- created: 2025-05-18T09:30:00+00:00
- updated: 2025-05-18T10:15:00+00:00
- similar_cases: []
