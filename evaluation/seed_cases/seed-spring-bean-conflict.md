# Spring Bean Name Conflict Between Auto-Configuration and Manual Bean

> case_id: `seed-spring-bean-conflict` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / Spring Framework 6.1
- jdk: 17

## Symptoms
Application fails to start with `BeanDefinitionOverrideException`. After adding a custom `ObjectMapper` bean for JSON customization, Spring Boot's auto-configured `JacksonAutoConfiguration` also defines an `ObjectMapper` bean with the same name. Spring Boot 2.1+ disables bean definition overriding by default.

## Error Log
```
org.springframework.beans.factory.support.BeanDefinitionOverrideException: Invalid bean definition with name 'objectMapper' defined in class path resource [cn/metast/lingxi/module/config/JacksonConfig.class]: Cannot register bean definition [Root bean: class [null]; scope=abstract; abstract=false; lazyInit=null] for bean 'objectMapper'. There is already [Root bean: class [null]; scope=abstract; abstract=false; lazyInit=null] bound.
    at org.springframework.beans.factory.support.DefaultListableBeanFactory.registerBeanDefinition(DefaultListableBeanFactory.java:1021)
    at org.springframework.boot.autoconfigure.jackson.JacksonAutoConfiguration$JacksonObjectMapperConfiguration.objectMapper(JacksonAutoConfiguration.java:98)
    at cn.metast.lingxi.module.config.JacksonConfig.objectMapper(JacksonConfig.java:25)
```

## Root Cause
`JacksonConfig` defines a `@Bean` method named `objectMapper()` that returns a customized `ObjectMapper`. Spring Boot's `JacksonAutoConfiguration.JacksonObjectMapperConfiguration` also defines a bean named `objectMapper`. Since `spring.main.allow-bean-definition-overriding` defaults to `false` in Spring Boot 2.1+, the container throws `BeanDefinitionOverrideException` at startup. The custom bean and the auto-configured bean have the same name, causing a conflict.

## Diagnosis Steps
1. Read startup error — `BeanDefinitionOverrideException` for bean `objectMapper`
2. Found custom bean in `JacksonConfig.java:25` — `@Bean public ObjectMapper objectMapper()`
3. Found auto-configured bean in `JacksonAutoConfiguration` — also named `objectMapper`
4. Confirmed: both beans register with name `objectMapper`, overriding is disabled
5. Verified `spring.main.allow-bean-definition-overriding` is not set (defaults to false)

## Fix Suggestion
1. Make the custom bean `@Primary` to indicate it should take precedence
2. Alternatively, use `@ConditionalOnMissingBean` on the auto-configuration (already in Spring Boot, but ensure ordering)
3. Rename the custom bean method to avoid the name clash: `@Bean("customObjectMapper")`
4. Do not set `spring.main.allow-bean-definition-overriding=true` — it masks real conflicts
5. Use `Jackson2ObjectMapperBuilderCustomizer` instead of defining a full `ObjectMapper` bean

## Tags
bean-conflict, spring-boot, objectmapper, bean-override, auto-configuration, jackson, startup-failure

---
- created: 2025-05-18T08:30:00+00:00
- updated: 2025-05-18T09:00:00+00:00
- similar_cases: []
