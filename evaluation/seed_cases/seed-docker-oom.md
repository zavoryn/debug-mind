# Docker Container OOM Kill Due to JVM Heap Exceeding cgroup Limit

> case_id: `seed-docker-oom` | severity: **critical** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2
- jdk: 17
- infrastructure: Docker 24.0 / Kubernetes 1.28

## Symptoms
Java application container gets OOMKilled by Kubernetes every few hours under moderate load. The container memory limit is set to 512Mi, but the JVM process uses more than 512Mi including heap, metaspace, thread stacks, and native memory. `kubectl describe pod` shows `Last State: Terminated with Reason: OOMKilled, Exit Code: 137`.

## Error Log
```
kubectl describe pod order-service-7d9f8b6c4f-x2k1p:
  Last State:     Terminated
    Reason:       OOMKilled
    Exit Code:    137
    Started:      Mon, 18 May 2025 06:00:00 UTC
    Finished:     Mon, 18 May 2025 08:32:15 UTC

Container metrics before kill:
  memory.usage_bytes: 536870912 (512 Mi)
  memory.limit_bytes: 536870912 (512 Mi)
  memory.fail_count:  847291

JVM settings:
  -Xmx384m -Xms128m -XX:MaxMetaspaceSize=128m
```

## Root Cause
The container memory limit is 512Mi, and JVM heap is set to 384Mi (`-Xmx384m`) with metaspace up to 128Mi. However, the JVM total memory footprint includes heap (384Mi) + metaspace (~128Mi) + thread stacks + native memory (direct buffers, JIT code cache, GC overhead), totaling well over 512Mi. When the JVM allocates native memory for NIO direct buffers or GC needs additional memory, the total exceeds the cgroup limit, triggering the OOM killer. The JVM heap settings do not account for off-heap memory overhead.

## Diagnosis Steps
1. Checked Kubernetes pod events — `OOMKilled` with exit code 137
2. Examined container metrics — memory usage at limit (512Mi)
3. Reviewed JVM startup flags — `-Xmx384m` but no `-XX:MaxRAMPercentage` set
4. Calculated total JVM memory: heap(384) + metaspace(128) + threads(~50) + native(~64) = 626Mi > 512Mi
5. Confirmed: JVM memory footprint exceeds container cgroup limit

## Fix Suggestion
1. Use `-XX:MaxRAMPercentage=75.0` instead of fixed `-Xmx` to let JVM auto-detect container limits
2. Increase container memory limit to 1024Mi: `resources.limits.memory: 1024Mi`
3. Reduce `-XX:MaxMetaspaceSize` to 96m if class loading is moderate
4. Limit direct buffer memory: `-XX:MaxDirectMemorySize=64m`
5. Add `-XX:+UseContainerSupport` (default in JDK 17, but explicit for clarity)
6. Set up memory alerting at 80% of container limit

## Tags
oom, docker, kubernetes, jvm, heap, container-limit, cgroup, memory, oomkilled

---
- created: 2025-05-18T06:00:00+00:00
- updated: 2025-05-18T06:45:00+00:00
- similar_cases: []
