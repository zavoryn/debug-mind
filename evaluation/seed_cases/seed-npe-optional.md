# Optional.get() on Empty Optional Causing NoSuchElementException

> case_id: `seed-npe-optional` | severity: **medium** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / Spring Data JPA
- jdk: 17

## Symptoms
The endpoint `GET /api/users/{id}` throws `NoSuchElementException` when a user ID is not found in the database. Instead of returning a 404, the API returns 500. The code uses `Optional.get()` without checking `isPresent()` or using safe alternatives.

## Error Log
```
java.util.NoSuchElementException: No value present
    at java.base/java.util.Optional.get(Optional.java:143)
    at cn.metast.lingxi.module.user.service.UserService.getUserById(UserService.java:78)
    at cn.metast.lingxi.module.user.controller.UserController.getUser(UserController.java:42)
    at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
    at org.springframework.web.method.support.InvocableHandlerMethod.invokeForRequest(InvocableHandlerMethod.java:171)
    at org.springframework.web.servlet.FrameworkServlet.service(FrameworkServlet.java:882)
```

## Root Cause
`UserService.getUserById()` calls `userRepository.findById(id).get()` directly without checking if the Optional contains a value. When the user ID does not exist, `findById()` returns `Optional.empty()`, and `.get()` throws `NoSuchElementException`. The code should use `orElseThrow()` with a domain-specific exception to return a proper 404 response, or use `ifPresentOrElse()` for conditional handling.

## Diagnosis Steps
1. Reproduced with a non-existent user ID — got `NoSuchElementException`
2. Traced stack to `UserService.java:78` — `userRepository.findById(id).get()`
3. Verified `findById()` returns `Optional.empty()` for non-existent IDs
4. Confirmed: `.get()` called on empty `Optional` without `isPresent()` check
5. Checked if `UserController` has proper exception handling — none for `NoSuchElementException`

## Fix Suggestion
1. Replace `optional.get()` with `optional.orElseThrow(() -> new UserNotFoundException(id))`
2. Add `@ExceptionHandler(UserNotFoundException.class)` in controller advice returning HTTP 404
3. Alternatively use `optional.orElse(null)` with explicit null check
4. Enable IDE inspection for `Optional.get()` without `isPresent()` check
5. Add unit tests for non-existent user ID scenario

## Tags
optional, nosuchelement, null-safety, java, optional-get, spring-data-jpa, defensive-programming

---
- created: 2025-05-18T17:00:00+00:00
- updated: 2025-05-18T17:30:00+00:00
- similar_cases: []
