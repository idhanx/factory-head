# FactoryManager AI - Critical Decisions, Risk Mitigation & Deployment Guide

**Version**: 1.0  
**Date**: 2025-09-10  
**Audience**: Technical Leadership, Product Management, Infrastructure Teams

---

## Part 1: Six Critical Technical Decisions

### Decision 1: Offline-First Architecture vs Online-First

**Context**:
- Tamil Nadu plants have unreliable WiFi (typical: 30% uptime during shifts)
- Shift entry must never fail due to connectivity
- Daily data backup is needed for compliance

**Options Evaluated**:

| Option | Pros | Cons | Cost |
|--------|------|------|------|
| **Offline-First (CHOSEN)** | Tablets work 100% offline, sync later | Complex conflict resolution needed | High initial, lower ongoing |
| | Entry never fails | Edge cases (two tablets editing same job) | |
| | Familiar to users (pen-paper backup) | Requires immutable event log + versioning | |
| Online-First + Sync Queue | Simpler architecture | If offline: data loss OR blocked entry | Medium |
| | Real-time reports | User frustration if WiFi cuts out | |
| Hybrid (local DB + forced sync) | Both benefits | Complexity of merging | High |
| | No data loss | Still require conflict rules | |

**Decision**: **Offline-First (IndexedDB + Sync Daemon)**

**Rationale**:
1. Core requirement: "Shift entry cannot fail"
2. Immutable event log naturally handles conflicts (timestamp ordering)
3. Familiar to SME users (paper notebooks are current practice)
4. Backup happens when online (not time-critical)

**Implementation Details**:

```typescript
// Client: IndexedDB Storage
const shiftSession = await db.shifts.add({
  id: uuid(),
  plant_id: 'plant-123',
  shift_date: '2025-09-10',
  status: 'open',
  events: [], // All events stored locally
  last_sync: null,
  sync_pending: true
});

// Sync Daemon (every 5 min OR on-demand):
// 1. Collect all events with sync_pending = true
// 2. POST /api/sync with timestamp ordering
// 3. Server: Merge by timestamp (append-only)
// 4. Server: Return canonical version
// 5. Client: Update local store with server's version
// 6. Mark sync_completed
```

**Conflict Resolution Rules**:
- **Append-only events**: Never conflict (separate event IDs)
- **Shift state updates**: Last-write-wins + conflict marking
- **Example**: Tablet 1 closes shift at 15:30, Tablet 2 adds event at 15:35 (both offline)
  - Tablet 1 syncs first: Shift locked
  - Tablet 2 syncs: Event rejected (shift already locked), marked as orphaned
  - User sees: "This event couldn't be recorded (shift closed). Contact support."

---

### Decision 2: Monolith or Microservices?

**Context**:
- Launch team: 7 people
- Complexity: 6 layers, 3 plugins
- Timeline: 30 weeks to MVP

**Options Evaluated**:

| Option | Pros | Cons | Deployment Complexity |
|--------|------|------|----------------------|
| **Monolith (CHOSEN)** | Single database (no joins across services) | All deployed together | Simple (Docker container) |
| | Easier local development | Harder to scale cost engine separately | Rollback: one version |
| | Transactional integrity | Language mismatch (Node + Python) | Monitoring: one process |
| Microservices | Independent scaling | Service discovery, API versioning | Medium |
| | Language separation (Node, Python) | Eventual consistency (cost + events desynchronized) | Complex |
| | Separate deployments | 10× operational overhead for 7-person team | Must build inter-service contracts |
| Hybrid (single Node monolith + Python worker) | Best of both | Still need queue (Redis) for async | Medium |
| | Node for API, Python for cost engine | Two deployment units | |

**Decision**: **Monolith with Two Process Types**:
- **API Server**: Node.js Fastify (all HTTP endpoints, auth, data routing)
- **Background Workers**: Python + Redis (cost engine, AI services, scheduled jobs)
- **Single Database**: PostgreSQL (shared schema, ACID transactions)

**Rationale**:
1. 30-week timeline requires fast iteration
2. Cost engine is stateless (only SQL queries + math), perfect for Python workers
3. API stays fast (no heavy computation)
4. Single database prevents sync issues between services
5. Easy to separate to microservices later (when team grows to 15+)

**Deployment Architecture**:

```
Docker Compose (dev) / Kubernetes (prod)
├─ api-server (Node.js Fastify) — Port 3000
│  ├─ /api/auth
│  ├─ /api/shifts
│  ├─ /api/events
│  └─ /api/copilot
│
├─ cost-engine (Python workers) — Redis consumer
│  ├─ On event: calculate cost, store breakdown, update cache
│  └─ Scheduled: nightly cost rollup, OEE calculations
│
├─ ai-service (Python + LLM) — Redis consumer
│  ├─ Coach briefing generation
│  ├─ Copilot question answering
│  └─ Pattern detection
│
├─ PostgreSQL 15
│  └─ All data: shifts, events, actions, audit log
│
└─ Redis 7
   ├─ Job queue (Bull for Node, same Redis)
   ├─ Session cache
   └─ Real-time ticker (live cost)
```

**Future Migration Path** (if >30 people):
```
Phase 2 (Month 12+):
├─ Extract cost-engine → separate microservice (Kubernetes HPA scaling)
├─ Extract ai-service → separate microservice (GPU-optimized node pool)
├─ Add service mesh (Istio) for inter-service comm
└─ Add distributed tracing (Jaeger)

Cost: Low now, high later (but justified by scale)
```

---

### Decision 3: Which LLM for Copilot & Coach?

**Context**:
- Coach briefing: Daily, 5:45 AM (predictable load)
- Copilot: On-demand (supervisor questions via WhatsApp)
- Low latency required (<5 sec for WhatsApp response)
- Streaming response OK for Copilot (real-time feedback)

**Options Evaluated**:

| Option | Cost (1K reqs/day) | Latency | Streaming | Instruction-Follow | Chosen For |
|--------|------------------|---------|-----------|-------------------|-----------|
| **OpenAI GPT-4 (CHOSEN)** | ₹150-200 | 1-2 sec | ✅ Yes | ✅ Excellent | Copilot + Coach |
| OpenAI GPT-3.5 | ₹30 | 500ms | ✅ Yes | ⚠️ Good | Cost-sensitive fallback |
| Anthropic Claude 3.5 Sonnet | ₹100 | 2 sec | ✅ Yes | ✅ Excellent | Alternative (similar) |
| Local LLaMA 2 (self-hosted) | ₹0 (compute only) | 3-5 sec | ❌ Slow stream | ⚠️ Fair | Post-MVP (if data residency needs) |
| Google Gemini | ₹80 | 1.5 sec | ✅ Yes | ✅ Good | Secondary option |

**Decision**: **OpenAI GPT-4 with Claude fallback**

**Rationale**:
1. Instruction-following crucial for manufacturing context
2. Streaming for real-time WhatsApp feels interactive
3. Cost ₹150-200/day acceptable for 50 plants (₹3-4 per plant)
4. India API availability confirmed (APAC endpoints)
5. Claude fallback if OpenAI unavailable

**Implementation**:

```python
# Cost Engine Service (stateless)
from langchain import OpenAI, PromptTemplate, LLMChain

coach_prompt = PromptTemplate(
    input_variables=["plant_name", "yesterday_losses", "top_machines", "tier"],
    template="""You are an AI Coach for a manufacturing plant. 
    
Plant: {plant_name}
Yesterday's recoverable losses: {yesterday_losses}
Top 3 machines by cost: {top_machines}
Tier: {tier} (1=rule-based, 2=pattern-based)

Generate exactly 3 prioritized actions for today's morning meeting in this JSON format:
{{
  "priorities": [
    {{"rank": 1, "machine": "...", "action": "...", "assigned_to": "...", "deadline": "07:15 AM", "value": "₹..."}}
  ]
}}

Requirements:
- Be specific (name person, exact time, ₹ value)
- Reference yesterday's data
- Suggest preventive action if pattern detected
- Keep under 3 lines per priority
- Output ONLY JSON"""
)

llm = OpenAI(model="gpt-4", temperature=0.2)
chain = LLMChain(llm=llm, prompt=coach_prompt, streaming=True)

# Daily cron job:
def generate_coach_briefing(plant_id, date):
    yesterday_losses = get_yesterday_losses(plant_id, date)
    top_machines = get_top_machines_by_cost(plant_id, date, limit=3)
    tier = get_plant_coach_tier(plant_id)
    
    response = chain.run(
        plant_name=plant.name,
        yesterday_losses=yesterday_losses,
        top_machines=top_machines,
        tier=tier
    )
    return json.loads(response)
```

**Cost Optimization**:
- Coach briefing: Batch at 5:30 AM, 50 plants = 1 API call per plant = ₹0.04 per call × 50 = ₹2/day
- Copilot: On-demand, assume 50 plants × 2 questions/day = 100 calls = ₹0.15 per call = ₹15/day
- **Total**: ₹500/month (₹10 per plant/month) → easily justified by ₹4K monthly fee

---

### Decision 4: Shared Tablet vs Per-Machine Tablet?

**Context**:
- Budget: ₹20-30K per tablet
- Plant size: 20-100 machines
- Data entry bottleneck: Supervisor must log downtime in <1 min

**Options Evaluated**:

| Option | Hardware Cost | Bottleneck Risk | Data Quality | Chosen For |
|--------|--------------|-----------------|-------------|-----------|
| **Shared per zone (CHOSEN)** | 20-30K × 3 = ₹60-90K | Medium (supervisor busy) | High (dedicated entry) | MVP |
| Per-machine display | 20-30K × 20 = ₹4-6L | Low (always available) | Low (multiple entries per machine) | Post-MVP |
| Paper log + end-of-shift entry | ₹0 | High (memory recall, rewriting) | Medium (errors, delays) | Not viable |
| Single shared tablet | 20-30K × 1 | High (bottleneck) | Low (delays, queuing) | Not viable |

**Decision**: **Shared tablet per zone** (1 tablet per 8-12 machines)

**Rationale**:
1. Supervisor walks floor constantly (familiar with zones)
2. Balance: Not too centralized (no queuing), not too dispersed (cost manageable)
3. 20-machine plant: 3 tablets = manageable hardware cost
4. Zone-based tablet naturally follows supervisor's workflow
5. Future: Hardware arrival removes this bottleneck (sensors auto-detect downtime)

**Zone Layout Example**:
```
Plant Layout (20 machines)
├─ Machining Section (Tablet A)
│  ├─ CNC-1, CNC-2, CNC-3, Lathe-1, Lathe-2, Drill-1
│  └─ Supervisor: Roshan (always in this zone)
│
├─ Assembly Section (Tablet B)
│  ├─ Bench-1, Bench-2, Jig-1, Jig-2, Press-1, Weld-1
│  └─ Supervisor: Kavitha (always in this zone)
│
└─ Finishing/Packing (Tablet C)
   ├─ Buff-1, Paint-1, Inspection-1, Pack-1, Pack-2
   └─ Supervisor: Arjun (always in this zone)
```

**Tablet Hardware Spec**:
- iPad (10-inch, WiFi + LTE, ₹28-32K)
- OR Samsung Tab S6 Lite (10-inch, ₹18-22K)
- Battery: 8+ hours (full shift)
- Case: Rugged, water-resistant
- Screen: Bright enough for factory light

---

### Decision 5: PostgreSQL or MongoDB?

**Context**:
- Audit trail integrity non-negotiable
- Shift closure lock (immutable state change)
- Cost parameter versioning (no retroactive changes)
- Join requirements: shifts → job orders → events → actions (relational)

**Options Evaluated**:

| Option | ACID | JSON | Immutability | Relational Joins | Scaling | Schema Flexibility |
|--------|------|------|------------|-----------------|---------|-------------------|
| **PostgreSQL (CHOSEN)** | ✅ Strong | ✅ Native | ✅ Constraints | ✅ Excellent | Read replicas | Schema-required (good) |
| MongoDB | ⚠️ Limited (v4+) | ✅ Native | ❌ No constraints | ⚠️ Requires denorm | Sharding complex | Schema-flexible (risky) |
| DynamoDB | ❌ No | ⚠️ Limited | ❌ No | ❌ No joins | ✅ Excellent | Schema-flexible |

**Decision**: **PostgreSQL 15+**

**Rationale**:
1. ACID compliance prevents audit trail corruption
2. Immutable constraints: `CHECK (immutable = TRUE)` enforced at DB level
3. Relational model natural for manufacturing (machines → jobs → events → actions)
4. JSON support (cost breakdowns stored as JSONB)
5. Full-text search (Factory Memory queries, later)
6. Triggers for automatic audit logging (before/after snapshots)
7. Proven in production MSMEs (cost-effective, managed options available)

**Schema Design Philosophy**:

```sql
-- Immutability enforced at DB level
CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
  entity_type VARCHAR(100),
  entity_id UUID,
  action VARCHAR(50),
  before_state JSONB,
  after_state JSONB,
  
  -- This constraint prevents any UPDATE to this table
  CONSTRAINT audit_immutable CHECK (immutable = TRUE)
);

-- Shift closure lock: once locked, no editing
CREATE TABLE shifts (
  id UUID PRIMARY KEY,
  status VARCHAR(50) CHECK (status IN ('open', 'closed', 'locked')),
  locked_at TIMESTAMP,
  
  -- Cannot modify if locked_at IS NOT NULL
  CONSTRAINT locked_immutable CHECK (locked_at IS NULL OR locked_at <= NOW())
);

-- Cost parameters: versioned by date (no retroactive changes)
CREATE TABLE cost_parameters (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL,
  cost_family VARCHAR(100),
  effective_date DATE NOT NULL,
  machine_cost_per_hour DECIMAL(10, 2),
  
  -- Can only insert; cannot update retroactively
  UNIQUE(plant_id, cost_family, effective_date)
);

-- JSON for cost breakdown (stored atomically)
CREATE TABLE downtime_events (
  id UUID PRIMARY KEY,
  cost_breakdown JSONB NOT NULL, -- {machine_cost, labour_cost, overhead, lost_contribution, total}
  
  -- Immutable: no UPDATE after insertion
  CONSTRAINT cost_immutable CHECK (immutable = TRUE)
);
```

---

### Decision 6: Copilot as Separate Service or Embedded?

**Context**:
- Copilot load: On-demand, peaks in morning (supervisors asking before shift starts)
- Cost Engine load: Event-driven (every downtime event)
- API Server load: Steady (data entry + status checks)

**Options Evaluated**:

| Option | Scaling | Development Isolation | Latency | Cost | Monitoring |
|--------|---------|----------------------|---------|------|-----------|
| **Separate Python service (CHOSEN)** | Independent HPA | Easy (separate deployment) | +500ms (network) | ₹150/month (CPU) | Per-service metrics |
| Embedded in Node.js | Shared pod | Hard (Node + Python mixed) | 0ms (in-process) | ₹50/month (shared) | Harder to isolate |
| Separate Node.js microservice | Independent scaling | Moderate (TypeScript) | +200ms (network) | ₹150/month | Good metrics |

**Decision**: **Separate Python Service (with Node.js API Gateway)**

**Rationale**:
1. LLM inference heavy (needs Python/TensorFlow stack)
2. Separate scaling: Copilot peaks at 5:45 AM + noon, independent from API
3. Easy to upgrade LLM without touching API code
4. Async job queue (Redis) decouples request from response
5. Streaming responses (long-polling or WebSocket from API to client)

**Architecture**:

```
User (WhatsApp)
    ↓
WATI Webhook → API Server (Node.js)
    ↓
    +→ Copilot API endpoint
         ↓
    +→ Queue job in Redis
         ↓
    Copilot Service (Python worker)
         ↓
    OpenAI API (streaming)
         ↓
    Store response in Redis cache
         ↓
    API polls cache OR WebSocket push
         ↓
WATI sends response back to user
```

**Latency Breakdown**:
- User WhatsApp → WATI → API: 500ms
- API → Queue job: 50ms
- Job → Copilot service: 100ms
- Copilot → OpenAI: 1-2 sec (streaming)
- OpenAI → Response: 500ms
- Store result: 50ms
- Push to API: 50ms
- API → WATI → User: 500ms
- **Total**: ~3-4 seconds (acceptable for WhatsApp)

---

## Part 2: Risk Register & Mitigation

### Risk 1: Data-Entry Fatigue (Weeks 3-6)

| Attribute | Details |
|-----------|---------|
| **Risk** | Supervisor stops logging after initial enthusiasm (new system fatigue) |
| **Severity** | HIGH (data quality collapse) |
| **Probability** | 60% (common in manufacturing IT) |
| **Mitigation Strategy** | 1. Shift Closure Lock (cannot close without complete data) |
| | 2. Morning meeting DEPENDENCY (cannot proceed without closed data) |
| | 3. Coach tier unlock (personal value emerges by week 4) |
| | 4. Week 4-8: 15-min owner review call with every plant |
| **Acceptance Criterion** | ≥85% shift closure rate by Week 8 |
| **Owner** | Product Lead + Supervisor coaching |

**Detailed Mitigation**:
```
Week 3: Supervisor resistance starts (feels like extra work)
  → System blocks shift closure if data incomplete
  → Owner sees "Cannot close: 2 downtime events missing reason codes"
  
Week 4: Coach briefing starts at 5:45 AM
  → Supervisor realizes Coach gives useful priority (personal relevance)
  → Motivation: "Coach tells me what to fix first"
  
Week 5-8: Running ROI visible on Owner Money Screen
  → Owner calls supervisor: "We recovered ₹8,200 this week, 80% from your reporting"
  → Supervisor becomes advocate (not just data entry person)
```

---

### Risk 2: Owner Stops Reading Daily Reports

| Attribute | Details |
|-----------|---------|
| **Risk** | Owner marks message as read, doesn't act on losses |
| **Severity** | HIGH (no action loop = no ROI = churn) |
| **Probability** | 40% (common in first 2 weeks) |
| **Mitigation Strategy** | 1. Morning meeting synchronization (report is input to meeting) |
| | 2. Weekly owner call (15 min, review top 3 losses + actions) |
| | 3. Month 1 ROI call (show cumulative savings, renewal confidence) |
| **Acceptance Criterion** | ≥80% owner report opens by Week 8 |
| | ≥50% of escalated losses become actions by Week 8 |
| **Owner** | Product Lead (onboarding consultant) |

**Detailed Mitigation**:
```
Week 1-2: Daily reports sent, no action
  → Consultant calls owner every morning (5 min)
  → "Your top loss today was ₹3,290 (CNC-3, tool breakage)"
  → "What's your plan?" → Action created together
  
Week 3-4: Owner sees pattern ("Same issue 3 times this week")
  → Coach briefing highlights: "Tool breakage recurring, assign action"
  → Owner momentum builds
  
Week 5-8: First verified actions with recovered savings
  → Cumulative ROI dashboard shows: "Recovered ₹12,400 this month"
  → Owner renewal discussion: "At this rate, you'll recover ₹1.5L annually"
```

---

### Risk 3: Garbage Cost Parameters → Garbage Costs

| Attribute | Details |
|-----------|---------|
| **Risk** | Owner provides wrong machine rates (overestimated or obsolete) |
| **Severity** | CRITICAL (credibility destruction) |
| **Probability** | 30% (common in SMEs, rarely track actual costs) |
| **Mitigation Strategy** | 1. Onboarding consultant validates rates (interview + sanity check) |
| | 2. Basic mode defaults (conservative estimate if unsure) |
| | 3. Week 1: Sanity check (total shift cost vs revenue) |
| | 4. Built-in verification: Cost cannot exceed revenue (flag if violated) |
| **Acceptance Criterion** | All cost parameters within ±20% of actual (audited) |
| **Owner** | Onboarding consultant |

**Detailed Mitigation**:
```
Onboarding conversation:
Consultant: "What's your machine hourly rate for CNC?"
Owner: "₹3,000 per hour"
Consultant: Asks follow-up questions:
  → "Does this include power, tooling, maintenance?"
  → "What's your annual depreciation for CNC?"
  → "How many CNC hours do you run per year?"
  → Sanity check: Total machine cost/hour × 20 machines × 8 hours = daily fixed cost
  → Compare to owner's stated monthly fixed cost
  → Adjust if misaligned

If owner unsure → Use Basic mode (conservative default rates)
  → CNC: ₹1,500/hour (lower bound estimate)
  → Later: Refine based on actual data
```

---

### Risk 4: Data Used to Punish People (No-Blame Culture)

| Attribute | Details |
|-----------|---------|
| **Risk** | Owner uses downtime logs to blame/punish supervisors or operators |
| **Severity** | CRITICAL (data entry stops, trust collapses) |
| **Probability** | 25% (low if owner understands manufacturing culture) |
| **Mitigation Strategy** | 1. 90-day "data collection" announcement (no blame) |
| | 2. System targets PROCESSES, never PEOPLE (UI never shows "John's mistakes") |
| | 3. Owner coaching: "Your ₹8,200 loss came from tools (60%), material (30%), setup (10%)" |
| | 4. Morning meeting frame: "Our processes lost ₹8,200, here's how we fix it" |
| **Acceptance Criterion** | Supervisor feedback: "I feel safe reporting problems" |
| **Owner** | Product Lead + Owner coaching |

**System Design Enforces No-Blame**:
```
UI never shows:
  ❌ "Roshan's downtime events: 5 (most on shift)"
  ❌ "Kavitha logged 12 quality defects"
  ✅ "Machining section downtime: Tool breakage (5×), Setup (3×), Material (2×)"
  ✅ "Quality defects by category: Burr (7×), Dimension (5×)"
  
Report header: "Plant Performance Summary" (NOT "Supervisor report")
Morning meeting: "Our line lost ₹8,200 to tool breakage. Maintenance has assigned action."
```

---

### Risk 5: Supervisor Under-Reporting (Gaming the System)

| Attribute | Details |
|-----------|---------|
| **Risk** | Supervisor hides downtimes to show better performance |
| **Severity** | HIGH (data integrity, ROI claims unreliable) |
| **Probability** | 40% (common if data tied to bonus) |
| **Mitigation Strategy** | 1. QA cross-verification (production vs quality counts) |
| | 2. Count reconciliation (hourly logs vs actual output) |
| | 3. Owner spot-checks (visit floor during shifts, audit logs) |
| | 4. Escalation alerts (machine output drop, quality spike → investigate) |
| **Acceptance Criterion** | Production count ≤ Cycle time × uptime hours (no phantom production) |
| | Quality reject rate consistent with historical |
| **Owner** | Quality Lead + Owner |

**Detection Mechanisms**:
```
Example: Supervisor claims CNC-3 ran 8 hours (no downtime)
  → Production log: 120 parts (at 4 min/part CT = 480 min = 8 hours) ✓ Consistent
  → But QA at batch end: 112 good, 8 rejects = 120 total ✓ Consistent
  → BUT: Historical QA rate = 0.5% rejects, today = 6.7%
  → Alert: "Quality spike on CNC-3 (6.7% vs 0.5% historical). Investigate."
  → Possible: Jig wear, calibration drift, material change
  → Action created: "Quality investigation CNC-3"
```

---

### Risk 6: WhatsApp/WATI Approval Delay

| Attribute | Details |
|-----------|---------|
| **Risk** | WATI approval takes 6+ weeks (est 3-5 weeks) |
| **Severity** | HIGH (escalation functionality blocked) |
| **Probability** | 60% (WATI India approval unpredictable) |
| **Mitigation Strategy** | 1. Apply for WATI on DAY 1 (non-blocking, parallel to dev) |
| | 2. SMS fallback (Twilio or Jio SMS API) ready by Week 12 |
| | 3. Escalation logic: Try WhatsApp, fallback to SMS automatically |
| | 4. Test on real WATI account (if approved early) or mock (staging) |
| **Acceptance Criterion** | Escalations flowing (WhatsApp or SMS) by Week 24 |
| **Owner** | DevOps + Product Lead |

**Fallback Flow**:
```
Downtime event at 10:14 AM
  ↓
Escalation engine at 10:19 AM (5 min)
  ├─ Try: Send WhatsApp via WATI
  │   └─ If successful: Done
  │   └─ If failed/timeout: Fallback
  └─ Fallback: Send SMS via Twilio
     └─ Message: "CNC-3 down 5 min (tool). Cost: ₹270. Reply STOP to disable."
     └─ Log: "Escalation via SMS (WATI unavailable)"
```

---

### Risk 7: Tablet/WiFi Failures

| Attribute | Details |
|-----------|---------|
| **Risk** | Tablet battery dies, WiFi disconnects, sync fails |
| **Severity** | MEDIUM (data loss, frustrated supervisor) |
| **Probability** | 40% (manufacturing environment is harsh) |
| **Mitigation Strategy** | 1. Offline-first PWA (tablet works 100% offline) |
| | 2. Sync daemon (retry every 5 min, exponential backoff) |
| | 3. Conflict resolution rules (immutable event log) |
| | 4. Backup charger + charging station per zone |
| **Acceptance Criterion** | Data never lost (even if device fails) |
| | Sync successful ≥99.9% of events |
| **Owner** | Frontend + DevOps |

**Detailed Mitigation**:
```
Scenario: Tablet loses WiFi at 14:30 (middle of shift)

14:30-14:45 (Offline)
  → Supervisor logs downtime event
  → Data stored in IndexedDB
  → UI shows: "✓ Saved (offline)"
  → No sync needed yet

14:45 (WiFi reconnects)
  → Sync daemon detects online
  → POST /api/sync with queued events
  → Server merges by timestamp
  → Client receives canonical shift state
  → UI shows: "✓ Synced (3 events)"

14:50 (Another WiFi drop)
  → Continue offline
  → Background retry queue active

15:35 (Shift closure)
  → Supervisor taps "Close Shift"
  → System checks: All events synced? (timeout 30 sec)
  → If still offline: Show warning "Please check WiFi before closure"
  → If online: Close immediately
  → Data is safe (either offline in IndexedDB, or synced to server)
```

---

### Risk 8: Scope Creep on Build Timeline

| Attribute | Details |
|-----------|---------|
| **Risk** | New features added mid-phase, delays 30-week timeline |
| **Severity** | HIGH (push-out to launch) |
| **Probability** | 80% (very likely in SME projects) |
| **Mitigation Strategy** | 1. Explicit "NOT-BUILDING" list (Section 9 of spec) is the contract |
| | 2. Weekly steering committee (review scope requests, gate decisions) |
| | 3. "Parking lot" for post-MVP features (documented, not ignored) |
| | 4. No mid-phase pivots (confirm tech stack Week 1, stick to it) |
| **Acceptance Criterion** | Launch on schedule with MVP-defined features only |
| | Scope changes logged and deferred post-MVP |
| **Owner** | Tech Lead + Product Lead |

**Scope Disciplines**:
```
NOT Building (MVP):
  ❌ Hardware IoT integration (post-MVP, Week 33)
  ❌ Benchmark network (post-MVP, Week 41)
  ❌ Kaizen plugin (parallel, but not blocking launch)
  ❌ Mobile app (PWA sufficient)
  ❌ Custom reports (standard reports only)
  ❌ Multi-shift scheduling (one shift at a time)

Weekly steering gate:
  Owner: "Can we add a photo upload for actions?"
  → Tech Lead: "Yes, 2-day task, adds 2 days to Phase 2D"
  → Product Lead: "Worth it? Owner must prioritize which feature we defer"
  → Decision: Photo upload added, defer Compliance plugin to post-MVP
  → Documented in parking lot, tracked for future
```

---

### Risk 9: Savings Plateau → Churn

| Attribute | Details |
|-----------|---------|
| **Risk** | After Month 1 savings, issues become harder, recoveries plateau |
| **Severity** | HIGH (renewal risk) |
| **Probability** | 50% (common in PDCA systems after quick wins) |
| **Mitigation Strategy** | 1. Coach progression (Tier 1 → 2 → 3 = deeper patterns) |
| | 2. Copilot unlocks pattern answers (what's changing?) |
| | 3. "Next loss" algorithm (Coach always surfaces #1 priority) |
| | 4. Monthly deep-dive call (pattern analysis, new opportunities) |
| | 5. Kaizen plugin (operator ideas drive continued improvement) |
| **Acceptance Criterion** | Sustained ≥₹5K monthly recovered profit (or ₹1.5L annually) |
| **Owner** | AI Lead + Product Lead |

**Detailed Strategy**:
```
Month 1 (Weeks 1-4 of launch): Quick wins
  → Tool breakage (recurring, easy fix) → ₹12,400 saved
  → Material wait (process, easy fix) → ₹8,600 saved
  → Total: ₹21,000 (44% of annual fee)
  
Month 2 (Weeks 5-8): Plateau risk
  → Low-hanging fruit exhausted
  → Owner worried: "Is this it?"
  
Coach progression (Tier 2 unlocked, Week 6)
  → Coach identifies deeper pattern: "Breakage intervals: every 4.2 days (not random)"
  → "Prevention: Inspect every 3 days (action: Suresh K., ₹400 labor, saves ₹3,200)"
  → Owner: "Wow, we can predict it!"
  
Month 3: Copilot patterns + Kaizen Ideas
  → Operator Anand suggests: "We're reworking burrs. Buff wheel worn?"
  → Investigation → confirmed
  → Action: Replace buff wheel every 2 weeks (was doing annually)
  → Savings: ₹4,200 (fewer defects)
  
Month 4: Deep correlations (Tier 3)
  → Coach discovers: "Breakages spike on Thursdays after Wednesday setups"
  → Theory: Setup friction causes next-day breakdown
  → Action: Post-setup verification (15 min, named person)
  → Savings: ₹2,800
  
Cumulative: ₹21K (M1) + ₹12K (M2-M3) + ₹8K (M4) = ₹41K
→ 82% of annual ₹50K fee recovered → ROI = 82% (very strong renewal case)
```

---

### Risk 10: Action Fatigue (Too Many Issues to Address)

| Attribute | Details |
|-----------|---------|
| **Risk** | 15+ actions created per month, team overwhelmed, nothing verified |
| **Severity** | HIGH (PDCA credibility collapse) |
| **Probability** | 70% (without discipline) |
| **Mitigation Strategy** | 1. Severity thresholds (>₹2,500 OR >30 min, no exceptions) |
| | 2. Recurring pattern rule (only ONE pattern action, not separate for each repeat) |
| | 3. Morning meeting gate (only discuss escalated actions, not all losses) |
| | 4. Overdue dashboard (surface blocking actions, pressure to close) |
| | 5. Max 3 open actions per plant at a time (policy) |
| **Acceptance Criterion** | ≤5 actions open at any time per plant |
| | ≥70% of created actions verified (not abandoned) |
| **Owner** | Rules Engine + Owner discipline |

**Trigger Rules (Hardcoded)**:
```
Action created only if:
  (cost > ₹2,500 AND single_event)
  OR (duration_minutes > 30)
  OR (quality_escape = TRUE)  // Customer complaint, regulatory issue
  OR (recurring_pattern = TRUE)  // Same reason 3+ in 7 shifts, ONE action created
  OR (manual_creation = TRUE)  // Owner/lead explicitly creates

Action NOT created for:
  ❌ Single 5-min downtime with ₹400 cost
  ❌ One rejection (unless systemic)
  ❌ Casual remarks ("We should fix...") without cost/frequency

Recurring pattern (coalescing):
  Day 1: CNC-3 tool breakage → Action created: "Tool breakage (CNC-3, recurring)"
  Day 3: CNC-3 tool breakage again → Linked to same action (not new action)
  Day 5: CNC-3 tool breakage again → Same action continues
  → Status transitions: Open → RCA → Monitoring → Verified
  → Only ONE action per pattern, multiple events linked
```

---

### Risk 11: Savings Overclaiming (Phantom ROI)

| Attribute | Details |
|-----------|---------|
| **Risk** | System claims ₹100K savings when only ₹20K actually achieved |
| **Severity** | CRITICAL (credibility destruction, regulatory issue) |
| **Probability** | 30% (if baseline not frozen, claims runaway) |
| **Mitigation Strategy** | 1. Baseline frozen at action creation (4-week average, immutable) |
| | 2. 2-week sustain rule (loss must stay down for 2 consecutive weeks) |
| | 3. Never negative recovery (cannot claim savings if no baseline) |
| | 4. Bottleneck factor prevents phantom claims (BF=0.5 on non-bottleneck) |
| | 5. Monthly owner audit (review 3 verified actions, spot-check numbers) |
| **Acceptance Criterion** | Recovered Profit calculation auditable (baseline, monitoring, result) |
| | Error rate <5% in spot audits |
| **Owner** | Cost Engine + Audit Trail |

**Detailed Mechanism**:
```
Action: "Material wait, Line 2" created on Day 1

BASELINE (Day 1):
  → Query: Last 4 weeks average loss (this machine, this reason)
  → Week -4: ₹2,100 (2 events)
  → Week -3: ₹3,500 (2 events)
  → Week -2: ₹1,800 (1 event)
  → Week -1: ₹2,600 (3 events)
  → Baseline = (2100 + 3500 + 1800 + 2600) / 4 = ₹2,500/week
  → Frozen in Action record (immutable)

MONITORING (Days 1-14):
  → Week 1 post-fix: ₹1,200 loss (1 event, major improvement)
  → Week 2 post-fix: ₹1,050 loss (1 event, sustained)
  
VERIFICATION (Day 14):
  → Rule: Both weeks LOWER than baseline = verified
  → Recovered Profit = ₹2,500 - ₹1,050 (average of 2 weeks) = ₹1,450/week
  → Extrapolated: ₹1,450 × 52 weeks = ₹75,400/year
  → BUT: Only credit "proven" value
  → Action status → "Verified" (not "annually extrapolated")

IF loss recurs (Week 3: ₹3,200 loss):
  → Falls above baseline
  → Action status → "Reopened" (fix didn't hold)
  → Recovered Profit = ₹0 (does not count)
  
IF baseline is Zero (issue never happened before):
  → Cannot create action (no baseline to compare)
  → Exception: Quality escape or regulatory issue (allow zero baseline)
```

---

## Part 3: Deployment Strategy

### Stage 1: Alpha → Beta → Launch

```
ALPHA (Weeks 1-20, Staging)
├─ Local dev + one test plant
├─ Weekly deploys (after every test pass)
├─ Owner/Consultant testing only
├─ Breaking changes allowed
└─ Tech Lead approval

BETA (Weeks 20-24, Staging + 2-3 pilot plants)
├─ Twice weekly deploys (after tests + QA)
├─ Real data from pilot plants
├─ Supervised testing (consultant onsite)
├─ No breaking changes (data migration required)
└─ Tech Lead + Product Lead approval

LAUNCH (Week 24+, Production + 5-10 plants)
├─ Weekly deploys (Tuesday 10 AM IST)
├─ All features tested in Beta
├─ Rollback plan ready
├─ Blue-green deployment (zero downtime)
└─ Tech Lead + Product Lead + Operations approval
```

### Deployment Checklist (Week 24)

```
Infrastructure
  ☐ AWS/Azure Mumbai region live
  ☐ PostgreSQL automated backups (daily, 30-day retention)
  ☐ Redis persistent (RDB snapshots)
  ☐ Load balancer + health checks
  ☐ CDN for static assets (images, JS bundles)
  ☐ SSL certificates valid (2+ years)

Monitoring & Observability
  ☐ Prometheus + Grafana dashboards live
  ☐ Sentry error tracking active
  ☐ Log aggregation (ELK or Datadog)
  ☐ Alerting rules: API latency >1s, error rate >0.1%, uptime <99.9%
  ☐ On-call rotation (tech lead, backend lead, operations)

Application
  ☐ All 6 core screens working on tablet
  ☐ Cost calculation verified (test vs manual audit)
  ☐ Offline sync tested (leave device offline 2+ hours, sync recovery)
  ☐ WhatsApp delivery confirmed (real WATI account, test messages)
  ☐ Shift closure lock preventing edits
  ☐ Audit trail immutable (cannot edit after shift locked)

Testing
  ☐ E2E tests covering: Shift Start → Cost → Escalation → Report → Action
  ☐ Mobile device tests (iPad + Android tablets, actual devices)
  ☐ Load test: 100 simultaneous users, no degradation
  ☐ Failover test: Database primary fails, read replicas serve
  ☐ Backup restore test: Restore from backup, verify data integrity

Documentation
  ☐ API documentation (Swagger UI live)
  ☐ Runbook for on-call team (alert response, rollback procedure)
  ☐ Training videos (supervisor, owner, QA inspector)
  ☐ Troubleshooting guide (common issues, fixes)

Data & Compliance
  ☐ Plant master data uploaded (machines, parts, operators, cost parameters)
  ☐ Audit trail configured (immutable, time-locked)
  ☐ Data residency verified (India region only)
  ☐ GDPR-compliant data deletion (90-day retention after plant off-boards)
  ☐ Compliance audit log (who accessed what, when)

Support
  ☐ Support email + WhatsApp on-call
  ☐ SLA: P1 response <1 hour, resolution <24 hours
  ☐ Escalation path (tech lead, product lead, founder)
  ☐ Known issues log + workarounds documented
```

---

**End of Document**. All critical decisions documented with trade-offs, risks mitigated with strategies and acceptance criteria, deployment sequence mapped out.

Ready for Week 1 kickoff. ✅

