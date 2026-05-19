# Node.js EventEmitter Memory Leak in WebSocket Manager

> case_id: `seed-node-memleak` | severity: **high** | status: **fixed**

## Environment
- language: Node.js
- framework: Express 4.18 / ws 8.13
- runtime: Node.js 20.10

## Symptoms
The Node.js WebSocket gateway process slowly increases memory usage from 150MB to over 1.5GB over 24 hours. Eventually the process becomes unresponsive or gets killed by the OOM killer. Node emits `MaxListenersExceededWarning` in logs. The issue correlates with client connection count — each reconnection leaks event listeners.

## Error Log
```
(node:42) MaxListenersExceededWarning: Possible EventEmitter memory leak detected. 11 message listeners added to [WebSocket]. Use emitter.setMaxListeners() to increase limit
    at _addListener (node:events:582:17)
    at WebSocket.addListener (node:events:600:10)
    at WebSocketManager.handleConnection (/app/src/websocket/manager.js:78:16)
    at WebSocketServer.<anonymous> (/app/src/websocket/server.js:23:28)

Process heap:
  RSS: 1.52 GB
  HeapTotal: 1.48 GB
  HeapUsed: 1.41 GB
```

## Root Cause
In `WebSocketManager.handleConnection()`, new `message`, `close`, and `error` event listeners are attached to each WebSocket instance on every connection. However, when a client disconnects and reconnects, the old WebSocket object's listeners are not removed. Over time, thousands of orphaned listeners accumulate. The `message` handler captures the `socket` reference in its closure, preventing garbage collection. Each reconnection adds 3+ listeners that never get cleaned up.

## Diagnosis Steps
1. Monitored process memory — steady linear growth correlating with connection count
2. Took heap snapshot with `v8.writeHeapSnapshot()` — found thousands of `WebSocket` objects with listener arrays
3. Searched for `MaxListenersExceededWarning` in logs — confirmed listener accumulation
4. Traced `handleConnection()` — event listeners added but never removed on disconnect
5. Confirmed closure reference pattern preventing GC of old socket objects

## Fix Suggestion
1. Remove all listeners on disconnect: `socket.removeAllListeners()` in the `close` handler
2. Use `socket.once()` instead of `socket.on()` for one-time events
3. Track listener count per socket and warn if it exceeds a threshold
4. Implement a connection registry with automatic cleanup on disconnect
5. Add periodic listener count monitoring: `socket.listenerCount('message')`

## Tags
memory-leak, eventemitter, nodejs, websocket, listener, gc, heap-snapshot, max-listeners

---
- created: 2025-05-18T12:00:00+00:00
- updated: 2025-05-18T12:35:00+00:00
- similar_cases: []
