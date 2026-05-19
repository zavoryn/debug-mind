# CORS Configuration Error Blocking Frontend API Requests

> case_id: `seed-cors-error` | severity: **medium** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / Spring Security 6.2
- jdk: 17

## Symptoms
The frontend web application cannot make API requests to the backend. Browser console shows CORS error: "Access to XMLHttpRequest has been blocked by CORS policy". The `@CrossOrigin` annotation and `WebMvcConfigurer` CORS configuration are both present but conflict with Spring Security's CORS handling. Preflight `OPTIONS` requests return 403 Forbidden.

## Error Log
```
Browser console:
  Access to XMLHttpRequest at 'https://api.example.com/api/orders' from origin 'https://app.example.com' has been blocked by CORS policy: Response to preflight request doesn't pass access control check: No 'Access-Control-Allow-Origin' header is present on the requested resource.

Spring Security debug log:
  o.s.security.web.FilterChainProxy : Securing OPTIONS /api/orders
  o.s.s.w.a.Http403ForbiddenEntryPoint : Pre-authenticated entry point called. Rejecting access
  o.s.security.web.csrf.CsrfFilter : Invalid CSRF token found for http://localhost:8080/api/orders
```

## Root Cause
CORS is configured in two places: `@CrossOrigin` on the controller and a `WebMvcConfigurer.addCorsMappings()` bean. However, Spring Security processes requests before the MVC CORS handler. Since Spring Security does not have CORS explicitly enabled, it rejects the preflight `OPTIONS` request with a 403 response before the CORS headers can be added. The fix requires calling `.cors()` in the `SecurityFilterChain` to let Spring Security delegate CORS handling to the MVC configuration.

## Diagnosis Steps
1. Reproduced in browser — CORS error on preflight OPTIONS request
2. Checked backend response — OPTIONS returns 403 with no CORS headers
3. Examined Spring Security config — no `.cors(Customizer.withDefaults())` in filter chain
4. Confirmed CORS configured in MVC layer but Security filter intercepts first
5. Verified: Security filter chain order prevents MVC CORS handler from executing

## Fix Suggestion
1. Add `.cors(Customizer.withDefaults())` to the `SecurityFilterChain` bean configuration
2. Ensure `WebMvcConfigurer.addCorsMappings()` or a `CorsConfigurationSource` bean is defined
3. Remove `@CrossOrigin` annotations — use centralized CORS configuration instead
4. Configure allowed origins explicitly: `.allowedOrigins("https://app.example.com")`
5. Add integration test for CORS preflight to prevent regression

## Tags
cors, spring-security, preflight, options, cross-origin, web, spring-boot

---
- created: 2025-05-18T14:30:00+00:00
- updated: 2025-05-18T15:00:00+00:00
- similar_cases: []
