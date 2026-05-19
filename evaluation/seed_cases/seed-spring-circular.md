# Spring Boot Circular Dependency Between OrderService and InventoryService

> case_id: `seed-spring-circular` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / Spring Framework 6.1
- jdk: 17

## Symptoms
Application fails to start with `BeanCurrentlyInCreationException`. The error indicates a circular reference between `OrderService` and `InventoryService`. This started after a developer added a new `checkStock()` call in `OrderService` that required `InventoryService`, which already depended on `OrderService`.

## Error Log
```
org.springframework.beans.factory.BeanCurrentlyInCreationException: Error creating bean with name 'orderService': Requested bean is currently in creation: Is there an unresolvable circular reference?
    at org.springframework.beans.factory.support.DefaultSingletonBeanRegistry.beforeSingletonCreation(DefaultSingletonBeanRegistry.java:355)
    at org.springframework.beans.factory.support.AbstractBeanFactory.doGetBean(AbstractBeanFactory.java:325)
    at org.springframework.beans.factory.support.DefaultListableBeanFactory.preInstantiateSingletons(DefaultListableBeanFactory.java:975)

The dependencies of some of the beans in the application context form a cycle:
   - orderService defined in file [/app/classes/cn/metast/lingxi/module/order/OrderService.class]
   - inventoryService defined in file [/app/classes/cn/metast/lingxi/module/inventory/InventoryService.class]
```

## Root Cause
A circular dependency exists between `OrderService` and `InventoryService`. `OrderService` constructor-injects `InventoryService` to validate stock availability, while `InventoryService` constructor-injects `OrderService` to reserve items against pending orders. Since Spring Boot 2.6, circular references are disabled by default (`spring.main.allow-circular-references=false`). Constructor injection makes this an unresolvable cycle because neither bean can be created without the other.

## Diagnosis Steps
1. Read startup error — `BeanCurrentlyInCreationException` with clear cycle description
2. Examined `OrderService` constructor — injects `InventoryService`
3. Examined `InventoryService` constructor — injects `OrderService`
4. Confirmed bidirectional constructor injection forming a cycle
5. Identified the recent commit that added `InventoryService` dependency to `OrderService`

## Fix Suggestion
1. Extract shared logic into a new `StockReservationService` to break the cycle
2. Alternatively, convert one side to `@Lazy` field/setter injection as a quick fix
3. Best practice: redesign so `InventoryService` does not depend on `OrderService` — use events instead
4. Apply domain-driven design: introduce an application service that orchestrates both

## Tags
circular-dependency, spring-boot, bean-creation, constructor-injection, cycle, startup-failure

---
- created: 2025-05-18T09:00:00+00:00
- updated: 2025-05-18T09:45:00+00:00
- similar_cases: []
