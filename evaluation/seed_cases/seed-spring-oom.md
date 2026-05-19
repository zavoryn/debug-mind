# Spring Boot OOM from Large Hibernate Query Result Set

> case_id: `seed-spring-oom` | severity: **critical** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.1 / Hibernate 6.2
- jdk: 17
- database: MySQL 8.0

## Symptoms
The application pod gets OOMKilled in production every time the `/admin/reports/export` endpoint is called. Kubernetes restarts the pod. Heap dump analysis shows a single `ArrayList` holding over 2 million `ReportEntity` objects consuming 1.8GB of heap. JVM was configured with `-Xmx512m`.

## Error Log
```
java.lang.OutOfMemoryError: Java heap space
    at java.base/java.util.Arrays.copyOf(Arrays.java:3537)
    at java.base/java.util.ArrayList.grow(ArrayList.java:244)
    at java.base/java.util.ArrayList.add(ArrayList.java:453)
    at org.hibernate.loader.Loader.doQuery(Loader.java:932)
    at org.hibernate.loader.Loader.doList(Loader.java:2821)
    at org.hibernate.loader.Loader.listIgnoreQueryCache(Loader.java:2710)
    at org.hibernate.loader.Loader.list(Loader.java:2695)
    at org.hibernate.loader.hql.QueryLoader.list(QueryLoader.java:589)

Pod OOMKilled: Limit: 1Gi, Usage: 1.02Gi
```

## Root Cause
The `ReportExportService.exportAll()` method calls `reportRepository.findAll()` which uses Hibernate to load all rows from the `reports` table into memory as a single `List<ReportEntity>`. The table contains 2.3 million rows. Each `ReportEntity` with lazy-loaded associations occupies approximately 800 bytes, totaling 1.84GB. The JVM heap limit was set to 512MB (`-Xmx512m`), far below what is needed. No pagination, streaming, or scrollable results were used.

## Diagnosis Steps
1. Checked Kubernetes pod events — `OOMKilled` with limit 1Gi
2. Retrieved heap dump from volume mount — analyzed with Eclipse MAT
3. Found largest retained object: `ArrayList<ReportEntity>` with 2,300,000 entries
4. Traced allocation to `ReportExportService.exportAll()` → `reportRepository.findAll()`
5. Confirmed `reports` table row count: 2.3M, each entity ~800 bytes

## Fix Suggestion
1. Use Spring Data `Stream<ReportEntity>` with `@QueryHints(@QueryHint(name = "org.hibernate.fetchSize", value = "1000"))`
2. Implement `ScrollableResults` or Spring Data `Pageable` with batch processing
3. Write results incrementally to output stream instead of building full list in memory
4. Increase JVM heap as interim measure: `-Xmx2g`
5. Add `@BatchSize(size = 500)` annotation on the entity for association loading
6. Consider using `StatelessSession` for read-only export operations

## Tags
oom, spring-boot, hibernate, large-resultset, heap, pagination, stream, batch-processing, mysql

---
- created: 2025-05-18T14:00:00+00:00
- updated: 2025-05-18T14:45:00+00:00
- similar_cases: []
