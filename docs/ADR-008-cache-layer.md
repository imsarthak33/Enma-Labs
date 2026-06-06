# ADR-008 — Cache Layer: AWS ElastiCache for Redis (Serverless)

**Status:** Accepted
**Date:** 2026-06-06
**Deciders:** Engineering
**Consequences:** Phase 8 production deployment ships with ElastiCache; sidecar Redis is explicitly rejected.

---

## Context

Phase 8 requires an in-memory cache to:

1. Memoise expensive deterministic computations (tax verdict for an identical
   invoice fingerprint, GSTIN validation, vendor→client lookup).
2. Cache hot LLM responses keyed on input hash (extraction prompts are
   highly deterministic given the same image bytes).
3. Power the `slowapi` rate-limit storage backend (per-IP counters with TTL
   windows must be shared across all backend ECS tasks).

The deployment target is AWS ECS Fargate (`enma-prod` cluster) with the
`enma-backend` service starting at `desired=1` and scaling to `desired≥2`
the moment a second pilot firm onboards.

## Options considered

### Option A — Redis sidecar container in each ECS task

Add a `redis:7-alpine` container to the `enma-backend` task definition,
shared via the task's loopback network.

| | |
|---|---|
| **Capex** | $0/month — no managed service fee |
| **Latency** | Lowest possible (~150 µs over loopback) |
| **Durability** | Zero — cache evaporates on every task restart / deploy |
| **Shared state** | None — each task has an isolated cache |
| **Scaling story** | Breaks horizontal scaling: tasks would diverge on rate-limit counters, deduplicate the same LLM call, etc. |
| **Operational cost** | Hidden cost: every scaling event resets cache, raising LLM spend |

### Option B — AWS ElastiCache for Redis (Serverless)

Provision an ElastiCache Serverless cache in the same VPC, with a security
group that allows ingress only from the ECS task security group.

| | |
|---|---|
| **Capex** | ~$12–25/month at expected launch traffic (50 ECPUs/sec average) |
| **Latency** | ~400 µs intra-AZ — well within the 2000 ms `/worker/document` budget |
| **Durability** | Survives task restarts, deploys, version upgrades |
| **Shared state** | Single source of truth across all tasks |
| **Scaling story** | Cache stays warm during scale-out; rate-limit counters are globally consistent |
| **Operational cost** | Auto-scales ECPU and storage — no provisioning math |

### Option C — In-process LRU (e.g., `functools.lru_cache`)

Already used for `app.config.get_settings`. Inappropriate for shared state
and TTL-based eviction.

## Decision

**Adopt Option B — ElastiCache for Redis (Serverless).**

The sidecar approach saves ~$15/month but produces an architecturally
broken cache the moment we scale to two backend tasks: rate-limit counters
desync, identical LLM calls re-execute across tasks, and the cache hit
ratio collapses every deploy. The savings would be eaten by a single
extra extraction call per hour.

ElastiCache Serverless also keeps the deployment script simple — no need
to size nodes or pick a replication strategy at launch; AWS scales
compute and storage automatically.

## Implementation notes

* **VPC placement:** same default VPC as the ECS tasks; a dedicated
  subnet group covers all three AZs.
* **Security group:** `enma-redis-sg`, ingress port 6379 from
  `enma-ecs-tasks-sg` only.
* **Encryption:** TLS in transit (enabled by default for Serverless);
  encryption at rest with AWS-managed KMS.
* **Backup:** disabled at launch — cache contents are reproducible.
* **Client:** `redis.asyncio` (the official `redis-py` async layer).
* **Failure mode:** every cache call is wrapped in a try/except — if Redis
  is unreachable, the function falls through to the underlying
  computation. Cache is an optimisation, never a correctness boundary.

## Consequences

* +$15/month operational cost (acceptable for production).
* All cacheable functions must produce deterministic JSON-serialisable
  outputs (already true for tax verdicts, extraction responses).
* The `slowapi` limiter gets distributed rate counters for free.
* If we ever need sub-100 µs cache lookups (e.g., for a hot loop), we can
  add an in-process L1 cache in front of Redis — but not yet justified.
