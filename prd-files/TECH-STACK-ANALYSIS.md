# FactoryManager AI - Tech Stack & Architecture Plan

> **SUPERSEDED by [ARCHITECTURE.md](ARCHITECTURE.md) v2.0 (2026-09-10).**
> Retained for its data-flow walkthrough (Part 1.2) and pattern catalogue (Part 1.3).
> Do not build from Parts 2–3 (stack and schema) — see Appendix B of ARCHITECTURE.md for the 14 substantive changes.


**Document Status**: Technical Architecture & Implementation Planning  
**Version**: 1.0  
**Date**: 2025-09-10  
**Target Audience**: Development Team, Technical Lead, Architects

---

## Executive Summary

FactoryManager AI is a **6-layer manufacturing intelligence platform** with:
- Real-time cost calculation for production events
- AI-driven decision prioritization (PDCA/RCA routing)
- WhatsApp-based escalations and conversational interface
- Accumulated plant-specific knowledge (Factory Memory)
- Built for offline operation on manufacturing floors in Tamil Nadu

**Launch product** = Stage 1 + Stage 2 (Stages 3 & plugins parallel)  
**Critical constraint**: 15-minute daily entry budget across all features

---

## Part 1: System Architecture

### 1.1 High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                   CLIENT LAYER                                   │
│  ┌────────────────────┬──────────────────┬───────────────────┐   │
│  │  React PWA         │  WhatsApp Bot    │  Admin Dashboard  │   │
│  │  (Tablets/Phones)  │  (WATI)          │  (Web)            │   │
│  │                    │                  │                   │   │
│  │ Offline-first      │  Conversational  │  Compliance       │   │
│  │ IndexedDB/SQLite   │  Interface       │  Monitoring       │   │
│  └────────────────────┴──────────────────┴───────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
                         ↕ REST/GraphQL API
┌──────────────────────────────────────────────────────────────────┐
│              BACKEND SERVICES LAYER                              │
│  ┌──────────────────┬─────────────────┬────────────────────────┐ │
│  │ API Gateway      │ Cost Engine     │ AI Services Layer      │ │
│  │ (Node.js         │ (Python)        │ (Python + LLM)         │ │
│  │ Express)         │ ├─ Cost calc    │ ├─ Copilot patterns    │ │
│  │                  │ ├─ OEE/KPIs     │ ├─ Coach briefings     │ │
│  │ WebSocket        │ ├─ Verification│ └─ Pattern detection   │ │
│  │ Real-time        │ └─ Bottleneck  │                        │ │
│  │                  │    factors     │                        │ │
│  └──────────────────┴─────────────────┴────────────────────────┘ │
│  ┌──────────────────┬─────────────────┬────────────────────────┐ │
│  │ Rules Engine     │ Message Queue   │ Background Jobs        │ │
│  │ (Drools/Node)    │ (Redis Streams) │ (Bull/Celery)          │ │
│  │ ├─ PDCA routing  │ ├─ Escalations │ ├─ Scheduled reports   │ │
│  │ ├─ Trigger rules │ ├─ Copilot req │ ├─ Coach generation    │ │
│  │ └─ Severity      │ └─ Async tasks │ └─ Memory indexing     │ │
│  │    classification│                │                        │ │
│  └──────────────────┴─────────────────┴────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                         ↕ Database API
┌──────────────────────────────────────────────────────────────────┐
│            DATA & INTELLIGENCE LAYER                             │
│  ┌──────────────────┬─────────────────┬────────────────────────┐ │
│  │ PostgreSQL 15+   │ Redis 7+        │ Vector DB (later)      │ │
│  │ ├─ Transactional │ ├─ Cache        │ ├─ Semantic search     │ │
│  │ │  events        │ ├─ Sessions     │ └─ Pattern matching    │ │
│  │ ├─ Audit trail   │ ├─ Rate limit   │                        │ │
│  │ ├─ Cost params   │ └─ Job queue    │ ElasticSearch (later)  │ │
│  │ └─ Masters       │                 │ ├─ Full-text search    │ │
│  │                  │                 │ └─ Event retrieval     │ │
│  └──────────────────┴─────────────────┴────────────────────────┘ │
│              Sync Service (Conflict Resolution)                  │
│              Audit Log (Immutable Event Stream)                  │
└──────────────────────────────────────────────────────────────────┘
```

### 1.2 Data Flow (Critical Path)

```
SHIFT START
├─ Supervisor taps "Start Shift"
│  └─→ Select job order, line, operators (pre-filled)
│      └─→ Shift record created locally + synced

DOWNTIME EVENT (Real-time)
├─ Supervisor taps "Machine Stopped"
│  └─→ Select reason from dropdown → log event
│      └─→ Cost calculated IMMEDIATELY (live ticker)
│          ├─→ Machine cost (idle running)
│          ├─→ Labour cost (idle operator)
│          ├─→ Overhead (factory continuing)
│          └─→ Lost contribution (parts never made × margin)
│              └─→ Applied bottleneck factor
│
├─ Escalation timer starts
│  ├─ 5 min:  Push notification to supervisor
│  ├─ 15 min: WhatsApp to Maintenance/Stores
│  └─ 30 min: WhatsApp to Plant Head with running cost

PRODUCTION/QUALITY ENTRY (Hourly/Batch)
├─ Supervisor/operator: count since last entry
├─ QA Inspector: good/reject counts with defect codes
│  └─→ Cross-verification: reconciliation check
│      └─→ Alert owner if mismatch > tolerance

SHIFT CLOSURE (End of shift)
├─ System blocks closure if:
│  ├─ Any downtime missing reason code
│  ├─ Any escalation unresolved
│  └─ Any production entry incomplete
│
├─ Once resolved, data LOCKS (no editing after this)
│  └─→ All events become read-only
│      ├─→ Audit trail complete
│      └─→ Cost figures final

END-OF-SHIFT REPORT (Automatic)
├─ System calculates:
│  ├─ Total recoverable loss (₹)
│  ├─ Ranked by machine + reason
│  ├─ Split: Recoverable vs Structural
│  └─→ Severity routing decision (triggers PDCA?)
│
├─ Owner receives:
│  ├─ WhatsApp report (shift end + 5 min)
│  ├─ Ranked losses (machine → reason → ₹)
│  └─ Running ROI (Recovered Profit ÷ fees paid)

ACTION CREATION (Triggered)
├─ Auto-triggers if:
│  ├─ Single event >₹2,500 OR >30 min
│  ├─ Same reason 3+ times in 7-shift window
│  └─ Manual creation by anyone
│
├─ Action lifecycle:
│  ├─ Open: Root cause unknown
│  ├─→ RCA assigned with 5-Why template
│  ├─→ Root cause established
│  ├─→ Countermeasure planned & executed
│  ├─→ Monitoring (2 weeks: does loss rate stay down?)
│  ├─→ Verified OR Reopened
│  └─→ 4-question closure prompt → Factory Memory

MORNING MEETING
├─ System displays:
│  ├─ Yesterday's locked losses (ranked by ₹)
│  ├─ Issues escalated to PDCA (>threshold only)
│  └─ Open Actions blocking Today's Plan
│
├─ Workflow:
│  ├─ Triage: Don't address every loss, only material ones
│  ├─ PDCA: Root cause established → Countermeasure assigned
│  └─ Gate: Can't proceed to Today's Plan until actions resolved

AI SHIFT COACH (Daily, 5:45 AM)
├─ Tier 0 (days 1-10): Silent
├─ Tier 1 (10 shifts): Rule-based briefing
│  ├─ Biggest loss yesterday
│  ├─ Same reason 3× without action
│  └─ Overdue actions
├─ Tier 2 (4-6 weeks): Pattern briefing
│  ├─ Breakage intervals (every X days)
│  ├─ Time-of-shift risk windows
│  └─ Prevention actions (named person, ₹ value)
└─ Tier 3 (12+ weeks): Deep patterns
   ├─ Day-of-week effects
   ├─ Job-specific problems
   └─ PM gap → breakdown correlation

COPILOT QUESTIONS
├─ Factual tier (always available):
│  ├─ "How much did we lose this week?"
│  ├─ "Which machine lost the most?"
│  └─ "What happened today?"
│
├─ Pattern tier (unlocked after history):
│  ├─ "Why is CNC-3 failing so much?"
│  ├─ "What usually fixes this defect?"
│  └─ "Which defect is increasing?"
│
└─ Answered via WhatsApp (same WATI channel as escalations)
```

### 1.3 Key Architectural Patterns

#### Pattern 1: Offline-First Sync
```
Local Device (IndexedDB/SQLite)
├─ All data available immediately
├─ Changes queued locally
├─ Sync daemon pushes every N minutes (or on demand)
│  └─→ Server resolves conflicts
│      └─→ Returns canonical version
│
└─ Conflict resolution:
   ├─ For events: append-only log (timestamp determines order)
   ├─ For state: last-write-wins with conflict marking
   └─ Audit trail: every sync logged with before/after
```

#### Pattern 2: Single Formula Implementation
```
CostCalculationService (immutable module)
├─ Versioned formula library
├─ Input: Event + Cost Parameters (frozen at shift date)
├─ Output: Structured cost breakdown
│  ├─ Machine cost: (D ÷ 60) × MCR
│  ├─ Labour cost: (D ÷ 60) × LCR
│  ├─ Overhead cost: (D ÷ 60) × OHR
│  ├─ Lost contribution: ⌊D ÷ CT⌋ × CM × BF
│  └─ Total: Sum of above
│
└─ Access: ALL screens/reports call this service
   ├─ Same value everywhere (single source of truth)
   ├─ Versioning for audit trail
   └─ Recalculation triggers on parameter change
```

#### Pattern 3: Data Source Abstraction
```
EventIngestionLayer (middleware)
├─ Input sources:
│  ├─ Manual: Supervisor typing on tablet
│  ├─ Later: IoT sensors, ERP feeds
│  └─ All normalized to unified Event model
│
├─ Unified Event model:
│  ├─ shift_id, machine_id, timestamp, event_type
│  ├─ event_data (JSON)
│  ├─ source (manual/sensor/erp)
│  └─ created_at, created_by
│
└─ Above layers never know source
   └─ When hardware arrives: just plug sensor feed in
```

#### Pattern 4: Severity-Based Routing
```
Morning Meeting Router
├─ Input: All losses from yesterday (ranked by ₹)
│
├─ Rule: Severity Threshold
│  ├─ PDCA threshold: >₹2,500 OR >30 min OR quality-escape
│  ├─ Recurring pattern: 3+ same reason in rolling 7 shifts
│  └─ Manual: Anyone can raise
│
├─ Output: Two buckets
│  ├─ Escalated to PDCA/RCA: Must be addressed today
│  └─ Logged but not escalated: No RCA work assigned
│
└─ Result: Meeting never has 10+ root causes to do
   └─ Only the material ones get PDCA attention
```

#### Pattern 5: Action Lifecycle = PDCA Cycle
```
PDCA Stage → Action State → What Happens
────────────────────────────────────────
PLAN        → Open        Problem defined with ₹ cost
  ↓         → Root Cause  Why it happened (5-Why template)
  ↓         → Countermeasure Planned Fix defined
  
DO          → In Progress Fix is being executed
  ↓         Photo/evidence attached
  
CHECK       → Closed-Monitoring System watches metrics for 2 weeks
  ↓         Did the loss rate actually drop?
  ↓         
ACT         → Verified  Loss stayed down; savings computed
  ↓         → Reopened  Loss recurred; fix didn't hold
  └─→ 4-Question Closure Prompt → Factory Memory
      (What fixed it? What changed? Photo? Check first next time?)
```

---

## Part 2: Recommended Tech Stack

### 2.1 Frontend Stack

| Layer | Technology | Why This Choice | Alternatives |
|-------|-----------|-----------------|--------------|
| **Framework** | React 18 + TypeScript | Component-driven, PWA-ready, strong ecosystem for tablets | Vue, Svelte |
| **PWA/Offline** | Workbox + IndexedDB | Service workers for offline, IndexedDB for structured data | react-query offline, PouchDB |
| **State Mgmt** | Redux Toolkit + RTK Query | Normalized cache, optimistic updates, time-travel debugging | Zustand, Jotai |
| **UI Components** | Material-UI (MUI) v5 | Robust, accessible, mobile-first, manufacturing-friendly | Ant Design, shadcn/ui |
| **Forms** | React Hook Form | Lightweight, performance, works offline | Formik, Zod |
| **Charts/Dashboards** | Apache ECharts or Recharts | Real-time data, large datasets, offline rendering | Chart.js, Plotly |
| **Sync Engine** | Custom (Node.js service) | Full control over conflict resolution, audit trail | CouchDB, WatermelonDB |
| **Mobile Considerations** | Responsive CSS + PWA | No app store needed, installable, offline-first | React Native (overkill for this) |

**Frontend Architecture Decision**: 
- **Single codebase** React PWA deployed to web + tablet browsers
- **No native app** needed initially (PWA + Android WebView sufficient)
- **Offline storage**: IndexedDB for tablet + SQLite.js (WASM) for structured queries
- **Sync service**: Separate service worker handling background sync

### 2.2 Backend Stack

| Layer | Technology | Why This Choice | Alternatives |
|-------|-----------|-----------------|--------------|
| **API Server** | Node.js (Fastify or Express) | Fast startup, excellent ecosystem, WebSocket for real-time | Go, Python (slower to start) |
| **Cost Engine** | Python + NumPy/Pandas | Vectorized calculation, audit trail preservation | Node.js (less ideal for math) |
| **Rules Engine** | Node.js + Neon (Rust rules) OR Drools | Complex conditional logic, easily testable | Custom Node.js (reinventing) |
| **LLM/AI Layer** | Python + LangChain + OpenAI/Anthropic | Proven chain-of-thought, prompt engineering patterns | HuggingFace local models |
| **Message Queue** | Redis Streams + Bull | Simple, Redis-native, perfect for escalations | RabbitMQ (overkill), Kafka (overkill) |
| **Background Jobs** | Bull (Node.js) + Redis | Same queue, integrated with Express | Celery (requires separate Python process) |
| **Authentication** | Firebase Auth OR Passport.js + JWT | Plant-scoped data, multi-tenant support | Auth0 (cost for SME scale) |
| **API Documentation** | OpenAPI 3.0 + Swagger UI | Auto-generated, interactive, testable | GraphQL (overkill for MVP) |

**Backend Microservices** (eventual, not MVP):
```
API Gateway (Node.js Fastify)
├─ Auth & rate limiting
├─ Request routing
└─→ Cost Service (Python)
│   ├─ Downtime calculation
│   ├─ OEE computation
│   └─ Verified savings
│
└─→ AI Service (Python + LLM)
│   ├─ Coach briefing generation
│   ├─ Pattern detection
│   └─ Copilot responses
│
└─→ Rules Service (Node.js)
│   ├─ PDCA routing
│   ├─ Action trigger rules
│   └─ Escalation logic
```

### 2.3 Database Stack

| Component | Technology | Purpose | Notes |
|-----------|-----------|---------|-------|
| **Primary** | PostgreSQL 15+ | ACID events, plant data, audit trail | JSON support, advanced indexing |
| **Cache/Session** | Redis 7+ | Real-time locks, session storage, rate limiting | Also job queue (Bull) |
| **Search** | PostgreSQL full-text (MVP) → Elasticsearch (later) | Event retrieval, pattern search | Postgres FTS sufficient for <100 plants |
| **Vector DB** | Weaviate or Pinecone (later, not MVP) | Factory Memory semantic search | After 6+ months of accumulation |

**Database Design Principle**: 
- **Immutable event log** for audit trail
- **Versioned cost parameters** (timestamp-based)
- **Job order spine** with routing support (multi-stage plants)
- **No direct updates after shift close** (append-only for integrity)

### 2.4 External Services

| Service | Provider | Purpose | Rationale |
|---------|----------|---------|-----------|
| **WhatsApp API** | WATI (WhatsApp India partner) | Escalations + Copilot | India regulatory requirement, 3-5 week approval |
| **SMS Fallback** | Twilio or Jio SMS API | Alerts during WATI delays | Cheap, reliable in India |
| **File Storage** | AWS S3 (public) or MinIO (self-hosted) | Master uploads, photos, evidence | Versioning, compliance |
| **Email** | SendGrid or Amazon SES | Compliance alerts, onboarding | Low volume, triggered |
| **LLM API** | OpenAI (GPT-4) or Anthropic Claude | Coach & Copilot | Streaming responses for real-time |

### 2.5 DevOps & Infrastructure

| Component | Technology | Why | Notes |
|-----------|-----------|-----|-------|
| **Container** | Docker | Consistent across dev/staging/prod | Multi-arch for India data centers |
| **Orchestration** | Kubernetes (EKS/AKS) OR Docker Compose | Multi-region, auto-scaling (later) | MVP can run on single instance |
| **Cloud Platform** | AWS (Mumbai region) OR Azure (India) | Local infrastructure, WATI integration | Data residency (India only) |
| **CI/CD** | GitHub Actions + ArgoCD | GitOps, audit trail, no manual deploys | Terraform for IaC |
| **Monitoring** | Prometheus + Grafana + Sentry | SLA compliance, error tracking | Cost tracking per plant |
| **Logging** | ELK Stack (or Datadog) | Centralized logs, audit queries | Immutable log retention |
| **Database Backup** | AWS RDS automated + Point-in-time recovery | Data safety, audit trail integrity | Daily backups, 30-day retention |

---

## Part 3: Database Schema (MVP)

### 3.1 Core Tables

```sql
-- Multi-tenant root table
CREATE TABLE plants (
  id UUID PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  location VARCHAR(255),
  industry VARCHAR(100), -- "auto-ancillary", "foundry", etc.
  subscription_status VARCHAR(50), -- "active", "trial", "inactive"
  costing_mode VARCHAR(50), -- "basic", "standard", "advanced"
  digital_readiness_score INT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  UNIQUE(name, location)
);

-- Master data: machines/stations
CREATE TABLE machines (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  station_id VARCHAR(50) NOT NULL, -- "CNC-3", "Press-1", "Assembly-L2"
  station_name VARCHAR(255),
  machine_type VARCHAR(100), -- "CNC turning", "milling", "welding", etc.
  section VARCHAR(100), -- "Machining", "Assembly", "Quality", "Dispatch"
  is_bottleneck BOOLEAN DEFAULT FALSE,
  cost_family VARCHAR(100), -- Links to cost parameters
  cost_rate_override DECIMAL(10, 2), -- Per-machine override if needed
  created_at TIMESTAMP,
  created_by UUID, -- Onboarding consultant
  UNIQUE(plant_id, station_id)
);

-- Master data: part numbers
CREATE TABLE parts (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  part_number VARCHAR(100) NOT NULL,
  description VARCHAR(500),
  customer_name VARCHAR(255), -- OEM name (for traceability)
  part_family VARCHAR(100), -- For defect code grouping
  status VARCHAR(50), -- "active", "obsolete", "imported"
  contribution_mode VARCHAR(50), -- "exact_rupee", "l_m_h_band"
  contribution_value DECIMAL(10, 2) OR JSON, -- ₹/part OR {L: 15, M: 35, H: 60}
  created_at TIMESTAMP,
  UNIQUE(plant_id, part_number)
);

-- Cycle times per part-operation
CREATE TABLE cycle_times (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  part_id UUID NOT NULL REFERENCES parts(id),
  operation VARCHAR(10), -- "Op10", "Op20", etc. (routing stage)
  cost_family VARCHAR(100), -- Which machine group
  cycle_time_minutes DECIMAL(10, 2),
  source VARCHAR(50), -- "route_card", "derived", "measured", "time_study"
  validation_status VARCHAR(50), -- "proposed", "validated", "superseded"
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

-- Routings for multi-stage plants
CREATE TABLE routings (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  part_id UUID NOT NULL REFERENCES parts(id),
  sequence_order INT,
  operation_code VARCHAR(10), -- "Op10", "Op20", ...
  operation_name VARCHAR(255),
  cost_family VARCHAR(100),
  next_operation_id UUID REFERENCES routings(id)
);

-- Employees/roles
CREATE TABLE employees (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  employee_id VARCHAR(100),
  name VARCHAR(255),
  role_category VARCHAR(100), -- "supervisor", "operator", "qa", "maintenance", "stores", "plant_head"
  whatsapp_number VARCHAR(20), -- +91XXXXXXXXXX
  escalation_level INT, -- 0=none, 1=always, 2=high-cost only
  created_at TIMESTAMP,
  UNIQUE(plant_id, employee_id)
);

-- Shift master
CREATE TABLE shifts (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  shift_name VARCHAR(100), -- "Shift A", "Shift B", "Night"
  start_time TIME,
  end_time TIME,
  break_duration_minutes INT,
  lunch_duration_minutes INT,
  day_off VARCHAR(10), -- "Sun", "Sat", etc.
  available_machine_hours_per_day DECIMAL(10, 2),
  sequence_in_day INT -- 1, 2, 3 for multi-shift plants
);

-- Cost parameters (versioned by plant + effective_date)
CREATE TABLE cost_parameters (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  cost_family VARCHAR(100),
  effective_date DATE NOT NULL, -- When these rates apply
  machine_cost_per_hour DECIMAL(10, 2), -- Depreciation + power + tooling + maintenance
  labour_cost_per_hour DECIMAL(10, 2), -- Operator + supervisor share
  overhead_cost_per_hour DECIMAL(10, 2), -- Factory overhead absorption
  input_source VARCHAR(100), -- "basic_blended", "standard_calculated", "user_entered"
  created_by UUID,
  created_at TIMESTAMP,
  UNIQUE(plant_id, cost_family, effective_date)
);

-- Reason codes for downtime
CREATE TABLE reason_codes (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  code VARCHAR(50),
  description VARCHAR(255),
  category VARCHAR(100), -- "tool", "material", "machine", "setup", "quality", "planned", "other"
  recoverable_flag BOOLEAN, -- TRUE = plant's fault, FALSE = unavoidable (structural)
  sequence_order INT, -- For UI button ordering (common reasons first)
  created_at TIMESTAMP,
  UNIQUE(plant_id, code)
);

-- Defect codes per part family
CREATE TABLE defect_codes (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  part_family VARCHAR(100),
  code VARCHAR(50),
  description VARCHAR(255),
  severity VARCHAR(50), -- "critical", "major", "minor"
  rework_possible BOOLEAN,
  sequence_order INT,
  created_at TIMESTAMP
);

-- ============ DAILY DATA TABLES ============

-- Shift session (top-level container)
CREATE TABLE shift_sessions (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  shift_id UUID NOT NULL REFERENCES shifts(id),
  supervisor_id UUID REFERENCES employees(id),
  date DATE NOT NULL,
  shift_number INT, -- 1, 2, 3 for multi-shift days
  status VARCHAR(50), -- "open", "closed", "archived"
  closed_at TIMESTAMP,
  closed_by UUID,
  locked_at TIMESTAMP, -- After closure, no editing
  created_at TIMESTAMP,
  UNIQUE(plant_id, date, shift_number)
);

-- Job order (start of shift record)
CREATE TABLE job_orders (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  shift_session_id UUID NOT NULL REFERENCES shift_sessions(id),
  job_number VARCHAR(100),
  part_id UUID NOT NULL REFERENCES parts(id),
  machine_id UUID NOT NULL REFERENCES machines(id),
  operator_id UUID REFERENCES employees(id),
  lot_id VARCHAR(100), -- Traceability: lot/batch number
  customer_name VARCHAR(255),
  quantity_planned INT,
  sequence_in_shift INT, -- Job 1, Job 2 if multiple changes
  started_at TIMESTAMP,
  ended_at TIMESTAMP,
  status VARCHAR(50), -- "running", "completed", "paused", "split"
  created_at TIMESTAMP
);

-- Production counts (hourly or per-batch entry)
CREATE TABLE production_counts (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  job_order_id UUID NOT NULL REFERENCES job_orders(id),
  machine_id UUID NOT NULL REFERENCES machines(id),
  count_since_last_entry INT,
  cumulative_count INT,
  recorded_at TIMESTAMP, -- When supervisor entered
  entry_timestamp TIMESTAMP, -- Actual clock time of event
  created_at TIMESTAMP
);

-- Downtime events (immediate entry, live cost calculation)
CREATE TABLE downtime_events (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  shift_session_id UUID NOT NULL REFERENCES shift_sessions(id),
  job_order_id UUID REFERENCES job_orders(id),
  machine_id UUID NOT NULL REFERENCES machines(id),
  reason_code_id UUID NOT NULL REFERENCES reason_codes(id),
  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP,
  duration_minutes INT, -- Computed: ended_at - started_at
  notes TEXT,
  recorded_by UUID REFERENCES employees(id),
  -- Cost breakdown (cached, computed by formula engine)
  machine_cost DECIMAL(10, 2),
  labour_cost DECIMAL(10, 2),
  overhead_cost DECIMAL(10, 2),
  lost_contribution_cost DECIMAL(10, 2),
  bottleneck_factor DECIMAL(3, 2), -- 0.5 to 1.0
  total_cost DECIMAL(10, 2), -- Sum of above
  cost_formula_version INT, -- For audit trail
  created_at TIMESTAMP,
  locked_at TIMESTAMP -- When shift closed
);

-- Quality records (per batch/operation completion)
CREATE TABLE quality_records (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  shift_session_id UUID NOT NULL REFERENCES shift_sessions(id),
  job_order_id UUID NOT NULL REFERENCES job_orders(id),
  part_id UUID NOT NULL REFERENCES parts(id),
  operation_code VARCHAR(10), -- "Op10", "Op20" for multi-stage
  inspector_id UUID REFERENCES employees(id),
  good_count INT,
  reject_count INT,
  total_count INT, -- good + reject
  defect_codes_json JSON, -- Array of {defect_id, count}
  disposition VARCHAR(50), -- "scrap", "rework", "hold"
  recorded_at TIMESTAMP,
  created_at TIMESTAMP
);

-- Scrap/rework cost records
CREATE TABLE scrap_records (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  quality_record_id UUID NOT NULL REFERENCES quality_records(id),
  scrap_count INT,
  material_cost DECIMAL(10, 2),
  processing_cost DECIMAL(10, 2), -- Operations completed before scrap
  total_scrap_cost DECIMAL(10, 2),
  created_at TIMESTAMP
);

CREATE TABLE rework_records (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  quality_record_id UUID NOT NULL REFERENCES quality_records(id),
  rework_count INT,
  rework_machine_id UUID REFERENCES machines(id),
  rework_time_minutes INT,
  rework_cost DECIMAL(10, 2),
  rework_completed_at TIMESTAMP,
  created_at TIMESTAMP
);

-- ============ ACTIONS & PDCA TABLES ============

-- Actions (linked to events that triggered them)
CREATE TABLE actions (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  downtime_event_ids UUID[], -- JSON array of linked event IDs
  action_type VARCHAR(50), -- "recurring", "single_event", "manual", "kaizen"
  issue_description TEXT,
  owner_id UUID NOT NULL REFERENCES employees(id),
  deadline DATE,
  status VARCHAR(50), -- "open", "root_cause_pending", "in_progress", "monitoring", "verified", "reopened", "closed"
  
  -- Root cause & solution
  root_cause TEXT, -- Why it happened
  root_cause_method VARCHAR(100), -- "5_why", "direct_knowledge", "ai_suggested"
  root_cause_set_at TIMESTAMP,
  root_cause_set_by UUID,
  
  countermeasure TEXT, -- How to fix it
  countermeasure_status VARCHAR(50), -- "planned", "in_progress", "completed", "not_applicable"
  
  photo_url VARCHAR(500), -- Evidence photo URL
  
  -- Baseline loss (frozen at action creation for verification later)
  baseline_weekly_loss DECIMAL(10, 2), -- Avg loss rate before fix
  baseline_period_start_date DATE,
  baseline_period_end_date DATE,
  
  -- Recovered profit (calculated post-verification)
  actual_weekly_loss_post_fix DECIMAL(10, 2),
  monitoring_start_date DATE,
  monitoring_end_date DATE,
  recovery_verified_at TIMESTAMP,
  recovered_profit_rupees DECIMAL(10, 2), -- Baseline - actual, conservatively
  recovery_sustained_weeks INT, -- Must be 2+ to verify
  
  created_at TIMESTAMP,
  created_by UUID,
  closed_at TIMESTAMP,
  closed_by UUID,
  
  INDEX(plant_id, status),
  INDEX(plant_id, owner_id)
);

-- RCA assignments (when root cause unknown at action creation)
CREATE TABLE rca_assignments (
  id UUID PRIMARY KEY,
  action_id UUID NOT NULL REFERENCES actions(id),
  assigned_to UUID NOT NULL REFERENCES employees(id),
  five_why_template TEXT, -- Pre-filled with event history
  deadline DATE,
  status VARCHAR(50), -- "pending", "in_progress", "submitted", "approved"
  findings TEXT,
  submitted_at TIMESTAMP,
  approved_at TIMESTAMP
);

-- Factory Memory: knowledge capture from verified actions
CREATE TABLE factory_memory (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  source_action_id UUID NOT NULL REFERENCES actions(id),
  
  -- The 4 questions asked at closure
  what_fixed_it TEXT,
  what_changed TEXT, -- Part / setting / material / method
  photo_url VARCHAR(500),
  check_first_next_time TEXT,
  
  -- Metadata for retrieval
  problem_category VARCHAR(100), -- "tool_breakage", "material_wait", etc.
  machine_type VARCHAR(100),
  part_family VARCHAR(100),
  
  -- Searchability
  embedding_vector VECTOR(1536), -- OpenAI embedding (later)
  search_text TEXT, -- Full-text search index
  
  captured_at TIMESTAMP,
  captured_by UUID,
  
  INDEX(plant_id, problem_category),
  INDEX(plant_id, machine_type)
);

-- ============ AUDIT & INTEGRITY TABLES ============

-- Immutable audit log (append-only)
CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
  user_id UUID REFERENCES employees(id),
  entity_type VARCHAR(100), -- "shift_session", "downtime_event", "quality_record", "action"
  entity_id UUID NOT NULL,
  action VARCHAR(50), -- "created", "updated", "closed", "locked"
  before_state JSONB, -- Previous state (for updates)
  after_state JSONB, -- New state
  reason_code VARCHAR(100),
  
  -- Immutable: created once, never updated
  CONSTRAINT audit_immutable CHECK (immutable = TRUE)
);

-- Sync log (for offline-first reconciliation)
CREATE TABLE sync_log (
  id UUID PRIMARY KEY,
  plant_id UUID NOT NULL REFERENCES plants(id),
  tablet_id VARCHAR(100), -- Device identifier
  sync_timestamp TIMESTAMP,
  operation_type VARCHAR(50), -- "push", "pull", "merge"
  events_pushed INT,
  events_pulled INT,
  conflicts INT,
  conflict_resolution VARCHAR(255), -- How conflicts were resolved
  error_log TEXT
);

-- Shift closure locks (prevent editing after closure)
CREATE TABLE shift_closures (
  id UUID PRIMARY KEY,
  shift_session_id UUID NOT NULL REFERENCES shift_sessions(id) UNIQUE,
  closed_at TIMESTAMP NOT NULL,
  locked_at TIMESTAMP NOT NULL,
  locked_by UUID NOT NULL REFERENCES employees(id),
  
  -- Closure checklist verification
  all_downtime_reasons_present BOOLEAN,
  all_production_counts_complete BOOLEAN,
  all_quality_entries_present BOOLEAN,
  all_escalations_resolved BOOLEAN,
  manual_override_used BOOLEAN,
  override_reason TEXT,
  
  CONSTRAINT locked_immutable CHECK (locked_at IS NOT NULL)
);
```

### 3.2 Key Indexing Strategy

```sql
-- Performance-critical indexes
CREATE INDEX idx_downtime_events_plant_date 
ON downtime_events(plant_id, DATE(started_at));

CREATE INDEX idx_production_counts_job_order 
ON production_counts(job_order_id, created_at);

CREATE INDEX idx_actions_plant_status 
ON actions(plant_id, status) INCLUDE (deadline, recovered_profit_rupees);

CREATE INDEX idx_audit_log_plant_timestamp 
ON audit_log(plant_id, timestamp DESC);

-- Full-text search on Factory Memory
CREATE INDEX idx_factory_memory_search 
ON factory_memory USING GIN(to_tsvector('english', search_text));

-- JSON operations (for cost breakdowns)
CREATE INDEX idx_downtime_cost_breakdown 
ON downtime_events USING GIN(jsonb_build_object(
  'machine_cost', machine_cost,
  'labour_cost', labour_cost
));
```

---

## Part 4: API Design (Critical Endpoints)

### 4.1 Event Ingestion API

```
POST /api/plants/{plantId}/shifts/{shiftId}/events
Headers: Authorization, X-Plant-Id
Body: {
  type: "downtime" | "production_count" | "quality_record" | "action_update",
  machine_id: UUID,
  timestamp: ISO8601,
  payload: { ... event-specific fields ... }
}

Response: {
  event_id: UUID,
  cost_calculated: true,
  total_cost: 3290,
  cost_breakdown: {
    machine_cost: 327,
    labour_cost: 235,
    overhead_cost: 141,
    lost_contribution: 2587
  },
  escalation_triggered: true,
  escalation_level: "15_min",
  message_queued: true
}
```

### 4.2 Shift Closure API

```
POST /api/plants/{plantId}/shifts/{shiftId}/close
Headers: Authorization, X-Plant-Id
Body: {
  supervisor_id: UUID,
  verified: true  // Only if all checks pass
}

Response: {
  shift_locked: true,
  total_recoverable_loss: 9380,
  losses_ranked: [
    { machine: "CNC-3", reason: "Tool Breakage", cost: 3290, count: 1 },
    { machine: "Line-2", reason: "Material Wait", cost: 2170, count: 1 },
    ...
  ],
  actions_created: 2,
  owner_report_queued: true,
  escalations_completed: 3
}
```

### 4.3 Copilot Query API

```
POST /api/plants/{plantId}/copilot/ask
Headers: Authorization, X-Plant-Id
Body: {
  question: "How much did we lose this week?",
  language: "en" | "ta",
  user_id: UUID  // For context (supervisor/owner/etc.)
}

Response: {
  answer: "₹41,200 recoverable loss this week, down ₹6,800 from last week...",
  tier: "factual" | "pattern",
  confidence: 0.95,
  data_points_used: 47,
  caveat: null  // Or "Only 3 days of data; pattern answers unlock after 30+ days"
}
```

### 4.4 Morning Meeting API

```
GET /api/plants/{plantId}/morning-meeting?date=2025-09-11
Headers: Authorization, X-Plant-Id

Response: {
  locked_losses_yesterday: {
    total_rupees: 9380,
    recoverable: 7010,
    structural: 2370
  },
  ranked_issues: [
    {
      rank: 1,
      machine: "CNC-3",
      reason: "Tool Breakage",
      duration: 47,
      cost: 3290,
      escalation_status: "escalated_to_pdca",
      action_id: UUID,
      action_status: "root_cause_pending"
    },
    ...
  ],
  open_actions_blocking_plan: 3,
  actions_board_link: "/actions?filter=open"
}
```

### 4.5 AI Coach Briefing API

```
GET /api/plants/{plantId}/coach-briefing?date=2025-09-11
Headers: Authorization, X-Plant-Id

Response: {
  tier: 1 | 2 | 3,
  briefing_version: "20250911_0545",
  priorities: [
    {
      rank: 1,
      machine: "CNC-3",
      category: "tool_breakage",
      context: "CNC-3 averages a tool breakage every 4.2 days. Today is Day 4 since the last one. 73% of breakages happen in the first 90 minutes of morning shift. Yesterday's cost: ₹3,290.",
      action: "Inspect and replace the insert on Station 3",
      assigned_to_id: UUID,
      assigned_to_name: "Suresh K.",
      deadline: "07:15 AM",
      prevention_value: 3200
    },
    ...
  ],
  delivery_method: "whatsapp" | "push_notification" | "both"
}
```

---

## Part 5: Build Phases & Timeline

### 5.1 Stage 1 (MVP): Core Capture + Cost Engine

**Duration**: Weeks 1-16 (4 months)  
**Team**: 2 full-stack + 1 backend (cost engine)  
**Acceptance Criteria**: One real plant running, shift closure lock working, cost engine producing correct figures

#### Phase 1A: Infrastructure & Foundation (Weeks 1-4)
- [ ] AWS/Azure account setup (Mumbai region)
- [ ] GitHub repo + CI/CD pipeline
- [ ] PostgreSQL + Redis setup + backup automation
- [ ] Authentication framework (OAuth2 + plant scoping)
- [ ] CORS & security headers configured
- [ ] Logging & monitoring stack (Prometheus + Grafana)
- [ ] Docker containers for all services
- [ ] WhatsApp Business API application (start now; 3-5 week approval)

**Deliverables**: 
- Docker Compose file for local development
- Initial API skeleton (Fastify + TypeScript)
- PostgreSQL schema (T1-T7 templates)
- Frontend React + Redux setup

#### Phase 1B: Master Data Upload (Weeks 5-8)
- [ ] T1-T7 template definitions (strict parsing)
- [ ] File upload UI (React component)
- [ ] CSV/Excel extraction service (Python)
- [ ] Data validation & error reporting
- [ ] "Found X machines, Y parts, Z operators..." confirmation screen
- [ ] Basic vs Standard costing mode setup
- [ ] Cost parameter entry forms

**Deliverables**:
- Upload component + extraction service
- Validation engine
- One plant data loaded & ready for Shift 1

#### Phase 1C: Core Screens & Data Entry (Weeks 9-12)
- [ ] Screen 1: Shift Start (select job, operators, parts)
- [ ] Screen 2: Machine Grid (live status, cost ticker)
- [ ] Screen 3: Downtime Log (reason dropdown, timestamps auto)
- [ ] Screen 4: Production Entry (count input, hourly reminder)
- [ ] Screen 5: Quality Entry (good/reject, defect codes)
- [ ] Offline storage (IndexedDB sync service)
- [ ] Tablet-optimized UI (thick fingers, poor light, speed)

**Deliverables**:
- 5 functioning screens on tablet
- Offline sync working
- Real plant entering data live

#### Phase 1D: Cost Engine & Reporting (Weeks 13-16)
- [ ] Cost calculation service (Python)
  - Downtime event cost formula
  - Machine / labour / overhead rates
  - Bottleneck factor application
  - Lost contribution calculation
- [ ] Screen 6: Shift Closure (lock, blocking rules)
- [ ] End-of-shift report (WhatsApp delivery)
- [ ] Owner Money Screen (4 numbers: Recovered ₹, Lost ₹, Actions, ROI)
- [ ] Cost verification (sanity checks against revenue)
- [ ] Reconciliation alerts (production vs quality counts)

**Deliverables**:
- Complete shift-level cost rollup
- Owner reports flowing via WhatsApp
- Plant producing correct cost figures

### 5.2 Stage 2: Discipline, Actions & Intelligence

**Duration**: Weeks 17-24 (8 weeks, parallel with Stage 1 end)  
**Team**: Add 1 rules engineer, 1 AI engineer  
**Acceptance Criteria**: Actions created, closed, verified; Morning meeting runs daily; Coach sending briefings

#### Phase 2A: Actions & PDCA Engine (Weeks 17-20)
- [ ] Action trigger rules (>₹2,500, >30 min, recurring pattern)
- [ ] Severity classification (Recoverable vs Structural)
- [ ] Action creation logic (auto + manual)
- [ ] Action lifecycle states & transitions
- [ ] RCA assignment (5-Why template with prefill)
- [ ] Root cause data entry
- [ ] Countermeasure planning
- [ ] Photo evidence upload

**Deliverables**:
- Actions board showing open/in-progress/monitoring
- RCA assignments flowing to team
- Closing prompt (4 questions) working

#### Phase 2B: Morning Meeting & Routing (Weeks 21-22)
- [ ] Screen 7: Morning Meeting Mode (big screen display)
- [ ] Issue triaging: Show all losses, only escalate material ones
- [ ] PDCA gate: Can't proceed to Today's Plan until escalated actions resolved
- [ ] Actions blocking list (overdue items highlighted)
- [ ] Close-action flow integration (RCA → closure → memory)

**Deliverables**:
- Daily morning meeting running
- Team addressing escalated issues only
- Precision routing reducing RCA fatigue

#### Phase 2C: Escalation Ladder & WhatsApp Integration (Weeks 23-24)
- [ ] WATI approval (by now, hopefully approved)
- [ ] Escalation engine (5 min → 15 min → 30 min)
- [ ] Supervisor push notification (5 min)
- [ ] Maintenance/Stores WhatsApp (15 min, role-based)
- [ ] Plant Head alert + running cost (30 min)
- [ ] Timestamp logging (nobody can say "I didn't know")
- [ ] SMS fallback (for WATI delays)

**Deliverables**:
- Escalation chain working
- WhatsApp messages flowing correctly
- All events tracked with timestamps

#### Phase 2D: Recovered Profit Calculation (Weeks 23-24, parallel)
- [ ] Baseline loss measurement (4-week average pre-action)
- [ ] Post-fix loss tracking (2-week sustain rule)
- [ ] Verified status logic (loss stayed down → savings credited)
- [ ] Reopened status logic (loss recurred → back to WIP)
- [ ] Owner home screen: Running ROI display
- [ ] Monthly owner report: Cumulative ₹ recovered vs annual fee

**Deliverables**:
- Actions showing verified savings
- Owner seeing measurable ROI
- Renewal argument (19× ROI) proven

### 5.3 Stage 3: Copilot Expansion

**Duration**: Weeks 25-32 (parallel with Stages 1-2)  
**Team**: 1 AI engineer + 1 backend engineer  
**Acceptance Criteria**: Copilot answering factual + pattern questions; unlock tiers working

#### Phase 3A: Copilot Factual Tier (Weeks 25-26)
- [ ] Question parsing (WhatsApp text → structured query)
- [ ] Factual answers (always available)
  - "How much did we lose this week?"
  - "Which machine lost the most?"
  - "What happened today?"
- [ ] Copilot API endpoint
- [ ] WhatsApp Bot endpoint (WATI webhook)
- [ ] Language support (English + Tamil via translation service)

**Deliverables**:
- Copilot answering basic questions
- Reusing same WATI channel as escalations

#### Phase 3B: AI Shift Coach Tier 1 (Weeks 27-28)
- [ ] Rule-based briefing generation
  - Biggest loss yesterday
  - Same reason 3+ times without action → flag it
  - Overdue actions → remind
- [ ] Coach API (generates briefing JSON)
- [ ] 5:45 AM scheduled delivery
- [ ] Supervisor phone receives briefing

**Deliverables**:
- Coach briefing sending daily
- Supervisor getting priority actions before shift

#### Phase 3C: Copilot Pattern Tier (Weeks 29-30)
- [ ] History accumulation logic (≥4 weeks of data)
- [ ] Pattern detection (breakage intervals, time-of-shift, defect trends)
- [ ] Confidence scoring ("I've seen 2 events; unlock after 30+ days")
- [ ] Copilot pattern answers
  - "Why does CNC-3 keep failing?"
  - "What usually fixes this defect?"
  - "Which defect is increasing?"

**Deliverables**:
- Pattern answers unlocking after sufficient history
- Copilot self-limiting confidently (saying when data is insufficient)

#### Phase 3D: Coach Tier 2 & 3 (Weeks 31-32)
- [ ] Coach Tier 2: Deep pattern briefings (4-6 weeks)
  - Breakage intervals ("every 4.2 days")
  - Risk windows ("73% happen first 90 min")
  - Prevention actions (named person, ₹ value)
- [ ] Coach Tier 3: Correlations (12+ weeks)
  - Day-of-week effects
  - Job-specific problems
  - PM gap → breakdown correlation

**Deliverables**:
- Coach progressing to data-grounded predictions
- Supervisor getting specific, valuable insights

### 5.4 Parallel: Three Plugins

**Start**: Week 9 (after Core data model solidifies)  
**Team**: 1 engineer per plugin  
**Key Constraint**: All read Core data only, all write to unified Actions engine

#### Compliance/Audit AI Plugin (Weeks 9-24)
- [ ] ISO 9001 / IATF 16949 / ZED clause mapping
- [ ] Evidence generation from Core events
  - Downtime log = CAPA evidence
  - Quality record = traceability evidence
  - Action closure = preventive action
- [ ] Audit readiness scoring
- [ ] CAPAssignments flow through Actions engine
- [ ] Scores displayed on compliance dashboard

#### Kaizen AI Plugin (Weeks 9-24)
- [ ] Operator idea submission (web form, eventually voice)
- [ ] Idea-to-proposal conversion (structured cost savings)
- [ ] Idea scoring & prioritization
- [ ] Kaizen Ideas → Actions (same engine)
- [ ] Realized savings credited to idea owner
- [ ] Recognition / incentive dashboard

#### Skill Matrix AI Plugin (Weeks 9-24)
- [ ] Training matrix (who trained on what)
- [ ] Link "No Operator" downtime → skill gaps
- [ ] Training needs ranked by ₹ loss prevented
- [ ] Training completion logged
- [ ] Metric: reduction in "No Operator" events

---

## Part 6: Risk Mitigation & Critical Decisions

### 6.1 Risk Register (from Section 12 of spec)

| # | Risk | Mitigation | Owner |
|---|------|-----------|-------|
| 1 | Data-entry fatigue (weeks 3-6) | Shift Closure Lock, morning meeting dependency, Coach gives personal value | Backend |
| 2 | Owner stops reading report | Track report opens; 15-min review call weeks 4-8 | Product Lead |
| 3 | Garbage cost parameters | Personal validation in onboarding; sanity-check totals; Basic defaults | Consultant |
| 4 | Data used to punish people | 90-day no-blame rule announced by owner; system targets processes, not names | Product Lead |
| 5 | Supervisor under-reporting | QA cross-verification; count reconciliation; owner spot-checks | Backend |
| 6 | WATI/WhatsApp approval delay | Apply Day 1 (3-5 weeks); SMS fallback in interim | DevOps |
| 7 | Tablet/WiFi failures | Offline-first app; sync on reconnect; conflict rules defined; one tablet per zone | Frontend |
| 8 | Scope creep on build | NOT-building list (Section 9) is the contract; monthly review of parking lot | Tech Lead |
| 9 | Savings plateau → churn | Coach must surface next loss; monthly report shows ROI cumulative vs annual fee | AI Lead |
| 10 | Action fatigue | Trigger thresholds low; severity routing; recurring → one pattern action; overdue surfaces | Backend |
| 11 | Savings overclaiming | Baseline frozen at creation; 2-week sustain; never negative; bottleneck factor prevents phantom claims | Cost Engine |

### 6.2 Critical Technical Decisions

#### Decision 1: Offline-First or Online-First?
**Choice**: **Offline-first** (PWA + IndexedDB)  
**Reason**: Tamil Nadu plant WiFi unreliable, tablets must work without internet  
**Trade-off**: Conflict resolution complexity, but manageable with immutable event log

#### Decision 2: Monolith or Microservices?
**Choice**: **Monolith MVP** (Node.js + Python services as separate processes, not separate deployments)  
**Reason**: Faster to MVP, easier to debug, lower operational overhead  
**Migration Path**: Separate microservices later when team grows

#### Decision 3: Which LLM for AI Coach & Copilot?
**Choice**: **OpenAI GPT-4 with fallback to Claude**  
**Reason**: Stream responses, good instruction-following, competitive pricing  
**Trade-off**: API-dependent, but plant has internet for reporting anyway

#### Decision 4: Shared Tablet or Per-Machine Tablet?
**Choice**: **Shared tablet per zone** (1 per 8-12 machines)  
**Reason**: Hardware cost (₹20-30K for 20-machine plant); tablets follow supervisors  
**Future**: Sensors later remove this bottleneck

#### Decision 5: PostgreSQL or MongoDB?
**Choice**: **PostgreSQL**  
**Reason**: ACID compliance (audit trail cannot be corrupted); JSON support (cost breakdowns); relational for joins  
**Trade-off**: More schema design upfront, but correctness is non-negotiable

#### Decision 6: Copilot as Separate Service or Embedded?
**Choice**: **Separate Python service** (LangChain + LLM)  
**Reason**: Complex prompt engineering, separate scaling needs, easier to upgrade  
**Integration**: API call from Node.js gateway; cached responses

---

## Part 7: Security & Compliance

### 7.1 Data Isolation (Multi-Tenant)

```sql
-- Every query includes plant_id as part of WHERE clause
-- No cross-plant data leakage possible

-- Authentication: Plant-scoped tokens
-- JWT payload includes: user_id, plant_id, role
-- Middleware rejects any request with mismatched plant_id
```

### 7.2 Audit Trail Immutability

```
- Audit log table: append-only (immutable constraint)
- Shift closure lock: once locked, no editing
- Cost parameters: versioned by effective_date (no retroactive changes)
- All state changes logged with before/after snapshots
- No DELETE operations; only logical soft-delete (status = 'archived')
```

### 7.3 Sensitive Data

- **Cost parameters**: Owner-visible only (not shared with operators)
- **Personal data**: Employee names logged in audit trail but restricted by role
- **Customer orders**: Part of traceability, included in compliance audit
- **WhatsApp numbers**: Never logged in error messages; encrypted in transit

### 7.4 Compliance by Region

- **India data residency**: All data in AWS/Azure India region only
- **No data export to US**: Separate region for US pilots (if any)
- **GDPR-adjacent**: Data deletion on plant cancellation (90-day retention for audit)
- **OEM compliance**: ISO 9001 / IATF 16949 built in (Compliance plugin)

---

## Part 8: Deployment & Release Strategy

### 8.1 Deployment Stages

| Stage | Environment | Deployment | Approval |
|-------|-----------|-----------|----------|
| **Alpha** | Local dev + one test plant | Hourly (git push) | None |
| **Beta** | Staging (AWS) + 2-3 pilot plants | Daily (after tests pass) | Tech Lead |
| **Launch** | Production + 5-10 plants | Weekly (Tuesday 10 AM IST) | Product Lead + Tech Lead |

### 8.2 Rollback Strategy

- **API changes**: Blue-green deployment, instant rollback if health checks fail
- **Database migrations**: Tested on staging; backward-compatible first; data migration second
- **Frontend**: Versioned PWA; old version served if new version breaks

### 8.3 Monitoring SLAs

```
- Shift entry latency: <500ms (end-to-end)
- Cost calculation: <1s (must be live)
- WhatsApp escalation: <30s (from event to message sent)
- Report generation: <5 min (end-of-shift reports)
- API uptime: 99.9% (alerts if down)
```

---

## Summary: What Needs to Start Now

### Week 1-2 Immediate Tasks

1. **Infrastructure Setup**
   - AWS/Azure account (India region)
   - PostgreSQL instance + backups
   - Redis instance
   - GitHub repo + team access
   - CI/CD pipeline (GitHub Actions)

2. **WhatsApp Business API**
   - Apply for WATI approval (non-negotiable, 3-5 weeks)
   - Have SMS fallback ready

3. **Team & Roles**
   - Tech Lead (full-time, architecture + decisions)
   - Backend engineer (Node.js + Python)
   - Frontend engineer (React PWA)
   - Cost engine engineer (Python)
   - DevOps engineer (Kubernetes + monitoring)

4. **Data Collection from Real Plants**
   - Get one pilot plant's T1 (machines) + T2 (parts) + T6 (reason codes)
   - Collect one week of manual production/downtime/quality logs
   - Get cost parameters (machine rate, labour, overhead)

5. **API & Database Skeleton**
   - Initialize Fastify + TypeScript backend
   - Create PostgreSQL schema (T1-T7 tables)
   - Set up JWT authentication + plant scoping
   - Create git branch protection rules

---

**Next Steps**: 
1. Review this document with the technical team
2. Confirm tech stack choices (frontend framework, backend framework, LLM provider)
3. Create detailed story cards for Stage 1 Phase 1A (infrastructure)
4. Set up weekly sync meetings (Tuesday 10 AM, 1 hour)

