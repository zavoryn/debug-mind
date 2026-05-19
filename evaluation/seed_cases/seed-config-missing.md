# Missing Config Property at Runtime in Spring Boot Application

> case_id: `seed-config-missing` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2
- jdk: 17
- config: application.yml / Nacos

## Symptoms
Application fails to start in the staging environment with `IllegalArgumentException: Could not resolve placeholder`. The property `app.storage.bucket-name` is defined in the production config but missing from the staging Nacos namespace. The property was added in the latest release but the staging config was not updated.

## Error Log
```
org.springframework.beans.factory.BeanCreationException: Error creating bean with name 'storageService': Injection of autowired dependencies failed; nested exception is java.lang.IllegalArgumentException: Could not resolve placeholder 'app.storage.bucket-name' in value "${app.storage.bucket-name}"
    at org.springframework.beans.factory.annotation.AutowiredAnnotationBeanPostProcessor.postProcessProperties(AutowiredAnnotationBeanPostProcessor.java:522)
    at org.springframework.beans.factory.support.AbstractAutowireCapableBeanFactory.populateBean(AbstractAutowireCapableBeanFactory.java:1437)

Caused by: java.lang.IllegalArgumentException: Could not resolve placeholder 'app.storage.bucket-name' in value "${app.storage.bucket-name}"
    at org.springframework.util.PropertyPlaceholderHelper.parseStringValue(PropertyPlaceholderHelper.java:180)
    at org.springframework.util.PropertyPlaceholderHelper.replacePlaceholders(PropertyPlaceholderHelper.java:126)
```

## Root Cause
`StorageService` uses `@Value("${app.storage.bucket-name}")` to inject the storage bucket name. This property was newly added in the current release and exists in `application-prod.yml` but was never added to `application-staging.yml` or the staging Nacos namespace. Spring Boot fails at bean creation time because there is no default value specified and the property is not found in any active property source.

## Diagnosis Steps
1. Read startup error — `Could not resolve placeholder 'app.storage.bucket-name'`
2. Searched codebase for the property — found in `StorageService.java` with `@Value`
3. Checked `application-prod.yml` — property exists with value `prod-assets-bucket`
4. Checked `application-staging.yml` — property is missing
5. Checked Nacos staging namespace — property not configured
6. Confirmed: new property not propagated to all environment configs

## Fix Suggestion
1. Add `app.storage.bucket-name` to `application-staging.yml` with staging-appropriate value
2. Add a default value as fallback: `@Value("${app.storage.bucket-name:default-bucket}")`
3. Consider using `@ConfigurationProperties` instead of `@Value` for type-safe config binding
4. Add config validation at startup with `@Validated` and `@NotNull`
5. Implement a config drift detection check in CI/CD pipeline to compare property keys across environments

## Tags
config, spring-boot, missing-property, @value, startup-failure, environment, nacos

---
- created: 2025-05-18T07:00:00+00:00
- updated: 2025-05-18T07:30:00+00:00
- similar_cases: []
