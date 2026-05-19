# Node.js Unhandled Promise Rejection Crashing Process

> case_id: `seed-node-unhandled` | severity: **critical** | status: **fixed**

## Environment
- language: Node.js
- framework: Express 4.18
- runtime: Node.js 20.10

## Symptoms
The Node.js API server process exits unexpectedly with exit code 1. Logs show `UnhandledPromiseRejectionWarning` followed by process termination. Starting from Node.js 15, unhandled promise rejections cause the process to exit by default. The issue is triggered when an external API call fails and the error is not caught.

## Error Log
```
node:internal/process/promises:288
            triggerUncaughtException(err, true /* fromPromise */);
            ^

TypeError: Cannot read properties of null (reading 'data')
    at PaymentService.processPayment (file:///app/src/services/payment.js:67:23)
    at process.processTicksAndRejections (node:internal/process/task_queues:95:67)

UnhandledPromiseRejection: Error: connect ETIMEDOUT 203.0.113.50:443
    at TCPConnectWrap.afterConnect [as oncomplete] (node:net:1555:16)
    at AxiosError.wrap (node_modules/axios/lib/core/AxiosError.js:163:28)

Node.js process exited with code 1 (unhandled promise rejection)
```

## Root Cause
`PaymentService.processPayment()` makes an HTTP call to an external payment gateway using `axios.post()`. When the payment gateway is unreachable (connection timeout), the promise rejects. The calling code uses `async/await` but the `catch` block only handles specific error types (e.g., `PaymentError`), not generic network errors like `ETIMEDOUT`. The unhandled rejection propagates to the event loop, and since Node.js 15+ terminates the process on unhandled rejections, the server crashes.

## Diagnosis Steps
1. Checked process manager logs — process exited with code 1
2. Found `UnhandledPromiseRejection` in stderr logs
3. Traced to `payment.js:67` — `response.data.transactionId` where `response` is null
4. Found that `axios.post()` threw `ETIMEDOUT` which was not caught by the specific error handler
5. Confirmed: network error type not covered in the catch block

## Fix Suggestion
1. Add a top-level catch-all in the async handler: `catch (error) { logger.error(error); ... }`
2. Register a global unhandled rejection handler: `process.on('unhandledRejection', ...)`
3. Add retry logic with exponential backoff for transient network errors
4. Validate `response` and `response.data` are non-null before accessing properties
5. Use `axios` interceptors to handle network errors globally
6. Consider using a circuit breaker pattern for the payment gateway

## Tags
unhandled-rejection, promise, nodejs, async-await, etimedout, process-crash, network-error

---
- created: 2025-05-18T13:30:00+00:00
- updated: 2025-05-18T14:00:00+00:00
- similar_cases: []
