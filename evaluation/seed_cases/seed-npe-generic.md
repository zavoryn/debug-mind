# Generic NullPointerException from Missing Null Check in DTO Mapping

> case_id: `seed-npe-generic` | severity: **medium** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2
- jdk: 17

## Symptoms
The REST API `/api/users/{id}/profile` returns HTTP 500 when the user's profile has not been fully set up. The error occurs because the profile DTO mapping code assumes all fields are non-null, but new users have incomplete profile data with several null fields.

## Error Log
```
java.lang.NullPointerException: Cannot invoke "java.lang.String.toLowerCase()" because the return value of "cn.metast.lingxi.module.user.dto.UserProfileDTO.getEmail()" is null
    at cn.metast.lingxi.module.user.service.UserProfileService.formatEmailAddress(UserProfileService.java:112)
    at cn.metast.lingxi.module.user.service.UserProfileService.getProfile(UserProfileService.java:67)
    at cn.metast.lingxi.module.user.controller.UserController.getProfile(UserController.java:45)
    at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
    at org.springframework.web.method.support.InvocableHandlerMethod.invokeForRequest(InvocableHandlerMethod.java:171)
```

## Root Cause
`UserProfileService.formatEmailAddress()` calls `dto.getEmail().toLowerCase()` without a null check. New users who registered via phone number do not have an email address, so `getEmail()` returns null. The code was written assuming email is always present (a valid assumption for the original registration flow), but a new phone-only registration feature was added later without updating all dependent code paths.

## Diagnosis Steps
1. Reproduced with a phone-only registered user — got NPE at UserProfileService.java:112
2. Traced `dto.getEmail()` — returns null when user registered via phone
3. Checked `formatEmailAddress()` — calls `.toLowerCase()` directly without null guard
4. Reviewed git history — phone registration feature added 2 weeks ago
5. Confirmed: new code path creates users without email, existing code assumes email is non-null

## Fix Suggestion
1. Add null check: `if (dto.getEmail() != null) { email = dto.getEmail().toLowerCase(); }`
2. Use `Optional.ofNullable(dto.getEmail()).map(String::toLowerCase).orElse(null)`
3. Add `@NotNull` validation on the email field for email-registration flow
4. Write unit tests covering phone-only registration users
5. Audit other methods in `UserProfileService` for similar null-safety issues

## Tags
npe, null-check, dto, mapping, validation, spring-boot, java

---
- created: 2025-05-18T16:00:00+00:00
- updated: 2025-05-18T16:30:00+00:00
- similar_cases: []
