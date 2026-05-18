# Spring Boot OOM on Large ResultSet Query

> case_id: `example002` | severity: **critical** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.1
- jdk: 17
- database: MySQL 8.0

## Symptoms
Application pod gets OOMKilled in production. Heap dump shows `ArrayList` with 2M+ `OrderEntity` objects. The endpoint `/admin/orders/export` triggers a full table scan without pagination.

## Error Log
```
java.lang.OutOfMemoryError: Java heap space
    at java.base/java.util.Arrays.copyOf(Arrays.java:3537)
    at java.base/java.util.ArrayList.grow(ArrayList.java:244)
    at org.hibernate.loader.Loader.doQuery(Loader.java:932)
    at org.hibernate.loader.Loader.doList(Loader.java:2821)
```

## Root Cause
`OrderRepository.findAll()` returns all rows as a single List. The `orders` table has 2.3M rows. No pagination or streaming was applied to the export endpoint. The JVM heap was 512MB which is insufficient for loading all entities at once.

## Diagnosis Steps
1. Checked pod logs → OOMKilled
2. Analyzed heap dump → ArrayList with 2M OrderEntity objects
3. Traced allocation to OrderExportService.exportAll()
4. Found it calls orderRepository.findAll() with no pagination
5. Confirmed table has 2.3M rows

## Fix Suggestion
1. Use `@Query("SELECT o FROM OrderEntity o")` with `Stream<OrderEntity>` return type
2. Process in chunks with `@BatchSize(size = 1000)` and `ScrollableResults`
3. Or use Spring Data `Pageable` with batch processing
4. Increase JVM heap as a temporary measure: `-Xmx2g`

## Tags
oom, spring-boot, hibernate, pagination, large-dataset, mysql, heap-dump, batch-processing

---
- created: 2025-05-18T14:20:00+00:00
- updated: 2025-05-18T15:00:00+00:00
- similar_cases: []
