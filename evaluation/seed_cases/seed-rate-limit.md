# Rate Limiter Misconfiguration Blocking Legitimate Traffic

> case_id: `seed-rate-limit` | severity: **medium** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / Bucket4j / Resilience4j
- jdk: 17

## Symptoms
Legitimate API users are being rate-limited far more aggressively than intended. Users report receiving HTTP 429 "Too Many Requests" after only 2-3 requests per minute. The intended limit was 100 requests per minute per user. The rate limiter is configured based on IP address but a reverse proxy header causes all users to share the same IP.

## Error Log
```
io.github.resilience4j.ratelimiter.RequestNotPermitted: RateLimiter 'apiRateLimiter' does not permit further calls
    at io.github.resilience4j.ratelimiter.RateLimiterPermission.waitForPermission(RateLimiterPermission.java:56)
    at io.github.resilience4j.ratelimiter.RateLimiter.decorateSupplier(RateLimiter.java:128)

HTTP response:
  HTTP/1.1 429 Too Many Requests
  X-RateLimit-Limit: 100
  X-RateLimit-Remaining: 0
  X-RateLimit-Reset: 1716028800
  Retry-After: 58

Access log showing shared IP:
  10.0.0.1 - user_id_123 - GET /api/orders - 429
  10.0.0.1 - user_id_456 - GET /api/products - 429
  10.0.0.1 - user_id_789 - GET /api/users - 429
```

## Root Cause
The rate limiter uses the client IP address as the rate limit key. However, all requests pass through a load balancer and reverse proxy (Nginx), which replaces the client IP with the proxy's internal IP (`10.0.0.1`). All users appear to come from the same IP, so they share a single rate limit bucket of 100 requests/minute. With 500+ concurrent users, the bucket is exhausted within seconds. The code should use `X-Forwarded-For` or `X-Real-IP` headers to extract the real client IP.

## Diagnosis Steps
1. Checked rate limiter configuration — 100 req/min per key, keyed by `request.getRemoteAddr()`
2. Examined access logs — all requests show same source IP `10.0.0.1` (the proxy)
3. Verified Nginx config — `X-Forwarded-For` header is set but application ignores it
4. Confirmed: `request.getRemoteAddr()` returns proxy IP, not real client IP
5. Traced rate limit key generation — uses remote address directly without header fallback

## Fix Suggestion
1. Extract real IP from headers: `request.getHeader("X-Forwarded-For")` with fallback to `getRemoteAddr()`
2. Configure Spring Boot `server.forward-headers-strategy=native` or `framework`
3. Use user ID (from JWT token) instead of IP as the rate limit key for authenticated endpoints
4. Configure Nginx to pass `X-Real-IP` header: `proxy_set_header X-Real-IP $remote_addr`
5. Add logging of the resolved rate limit key for debugging

## Tags
rate-limit, resilience4j, reverse-proxy, x-forwarded-for, spring-boot, throttling, ip-address

---
- created: 2025-05-18T16:30:00+00:00
- updated: 2025-05-18T17:00:00+00:00
- similar_cases: []
