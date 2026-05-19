# Python asyncio Deadlock in Concurrent Task Processing

> case_id: `seed-async-deadlock` | severity: **high** | status: **fixed**

## Environment
- language: Python 3.11
- framework: asyncio / aiohttp
- runtime: CPython 3.11.4

## Symptoms
An async web service stops processing requests after running for several minutes. All worker coroutines hang indefinitely. No error is raised; requests simply time out. The service uses `asyncio.gather()` to process a batch of tasks, each of which acquires an `asyncio.Lock()` and then awaits a sub-task that also tries to acquire the same lock.

## Error Log
```
ERROR:asyncio:Task was destroyed but it is pending!
task: <Task pending name='Task-87' coro=<process_batch() running at /app/services/batch_processor.py:45> wait_for=<Future pending cb=[Task.task_wakeup()]>>
WARNING:aiohttp.server:Request handler timed out after 300.0 seconds
    at aiohttp.web_protocol.handle_request(web_protocol.py:422)
    at asyncio.base_events.run_until_complete(base_events.py:636)
```

## Root Cause
The `process_batch()` coroutine acquires an `asyncio.Lock`, then within the locked section calls `asyncio.create_task()` to spawn a sub-task. The sub-task also attempts to acquire the same lock via `lock.acquire()`. Since asyncio runs on a single thread, the sub-task cannot proceed until the parent releases the lock, but the parent is awaiting the sub-task — a classic deadlock. The event loop is blocked waiting for the lock to be released, which will never happen.

## Diagnosis Steps
1. Observed all request handlers timing out after 300 seconds
2. Added `asyncio.get_running_loop().debug = True` to enable detailed task diagnostics
3. Used `asyncio.all_tasks()` dump to see all tasks stuck in `lock.acquire()`
4. Traced the lock acquisition pattern — parent holds lock and awaits child that needs same lock
5. Confirmed deadlock: lock is non-reentrant, creating a cycle

## Fix Suggestion
1. Replace `asyncio.Lock` with `asyncio.Semaphore(1)` if reentrant behavior is needed
2. Better: restructure code so the sub-task does not need the lock — pass data directly
3. Use `asyncio.timeout()` wrapper to prevent indefinite hangs
4. Consider using an `asyncio.Queue` to decouple the producer (lock holder) from consumer
5. Add lock acquisition timeout: `await asyncio.wait_for(lock.acquire(), timeout=10.0)`

## Tags
deadlock, asyncio, python, lock, concurrent-task, event-loop, reentrant, timeout

---
- created: 2025-05-18T11:00:00+00:00
- updated: 2025-05-18T11:40:00+00:00
- similar_cases: []
