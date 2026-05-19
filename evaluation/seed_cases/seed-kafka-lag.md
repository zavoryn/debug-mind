# Kafka Consumer Group Lag Spike During Peak Processing

> case_id: `seed-kafka-lag` | severity: **high** | status: **fixed**

## Environment
- language: Java
- framework: Spring Boot 3.2 / Spring Kafka 3.1
- jdk: 17
- middleware: Apache Kafka 3.6

## Symptoms
Kafka consumer group lag spikes from under 100 to over 500,000 messages during peak hours. Processing throughput drops from 5,000 msg/s to 200 msg/s. Consumer instances show high GC pause times. Some partitions show 0 consumption rate while others are overwhelmed. Rebalance events happen frequently.

## Error Log
```
WARN o.s.k.l.KafkaMessageListenerContainer - Consumer instance 'consumer-order-processor-1' rebalance event: partitions revoked [order-events-0, order-events-2, order-events-4]
WARN o.s.k.l.KafkaMessageListenerContainer - No offset found for partition order-events-3, reset to LATEST

org.apache.kafka.clients.consumer.CommitFailedException: Commit cannot be completed since the group has already rebalanced and assigned the partitions to another member.
    at org.apache.kafka.clients.consumer.internals.ConsumerCoordinator.sendOffsetCommitRequest(ConsumerCoordinator.java:878)
    at org.apache.kafka.clients.consumer.internals.ConsumerCoordinator.commitOffsetsSync(ConsumerCoordinator.java:799)

Kafka Consumer Lag:
  order-events-0: 142,856
  order-events-1: 0
  order-events-2: 198,234
  order-events-3: 0
  order-events-4: 175,912
```

## Root Cause
The `@KafkaListener` method `processOrderEvent()` performs synchronous HTTP calls to an external inventory service for each message. The external service has high latency (200-500ms per call) during peak hours. With `concurrency=3` and `max.poll.records=500`, the consumer processes messages too slowly to finish before `max.poll.interval.ms=300000ms` expires. This triggers a rebalance, which further delays processing as partitions are reassigned. The combination of synchronous external calls and low consumer concurrency creates a feedback loop of lag and rebalancing.

## Diagnosis Steps
1. Checked Kafka consumer lag metrics via `kafka-consumer-groups.sh` — lag at 500K+
2. Observed frequent rebalance events in application logs
3. Profiled `processOrderEvent()` — 80% of time spent on synchronous HTTP call
4. Verified `max.poll.interval.ms=300000` and `max.poll.records=500`
5. Confirmed: slow processing triggers rebalance, rebalance triggers re-processing

## Fix Suggestion
1. Replace synchronous HTTP calls with async non-blocking calls (WebClient)
2. Reduce `max.poll.records` from 500 to 50 to process smaller batches
3. Increase `max.poll.interval.ms` from 300s to 600s
4. Increase `concurrency` from 3 to 6 to match partition count
5. Implement manual offset management with `Acknowledgment.acknowledge()` for at-least-once
6. Add circuit breaker for the external inventory service call

## Tags
kafka, consumer-lag, rebalance, spring-kafka, throughput, poll-interval, backpressure

---
- created: 2025-05-18T15:00:00+00:00
- updated: 2025-05-18T15:40:00+00:00
- similar_cases: []
