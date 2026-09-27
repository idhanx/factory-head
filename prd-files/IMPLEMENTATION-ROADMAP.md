# FactoryManager AI - Implementation Roadmap & API Specifications

**Version**: 1.0  
**Date**: 2025-09-10  
**Team Distribution**: Full 30-week build plan with detailed week-by-week breakdown

---

## Part 1: Team Structure & Roles

### 1.1 Core Team (MVP Launch)

| Role | Count | Responsibilities | Seniority |
|------|-------|-----------------|-----------|
| **Technical Lead** | 1 | Architecture decisions, code reviews, escalations | 10+ years |
| **Backend Engineer (Node.js)** | 1 | API, auth, database, integrations | 5+ years |
| **Backend Engineer (Python)** | 1 | Cost engine, AI services, data pipelines | 5+ years |
| **Frontend Engineer** | 1 | React PWA, offline storage, mobile UX | 4+ years |
| **DevOps Engineer** | 1 | Infrastructure, CI/CD, monitoring, security | 4+ years |
| **Product Lead** | 1 | Spec clarity, plant liaison, demo prep | 3+ years |
| **QA / Test Automation** | 1 | E2E tests, cost verification, mobile testing | 2+ years |

**Total**: 7 FTE  
**Budget**: ₹18-24 Lakhs/month (INR rates)  
**Timeline to launch**: 30 weeks (7.5 months)

### 1.2 Extended Team (Later Phases)

- AI/ML Engineer (GPT/LLM fine-tuning) — Week 17 onwards
- Data Analyst (pattern detection, Factory Memory) — Week 25 onwards
- UI/UX Designer (workflow optimization) — Weeks 9-16
- QA (manual testing on devices) — Weeks 13+ (parallel)

---

## Part 2: Detailed Build Phases with Week-by-Week Breakdown

### Phase 1: Foundation (Weeks 1-4)

#### Week 1: Kickoff & Infrastructure
**Goals**: AWS setup, repo initialized, CI/CD pipeline running

**Tasks**:
- [ ] Tech Lead: Architecture review, stack confirmation, team sync cadence established
- [ ] DevOps: AWS account creation (Mumbai region), VPC/subnets, RDS PostgreSQL (managed), Redis Elasticache, S3 bucket
- [ ] DevOps: DNS registration, SSL certificates, load balancer configuration
- [ ] Backend (Node): GitHub repo init, ESLint/Prettier, Fastify skeleton
- [ ] Backend (Node): TypeScript configuration, tsconfig, build scripts
- [ ] DevOps: GitHub Actions CI/CD pipeline (lint, test, build, push to ECR)
- [ ] Frontend: React app init, Redux setup, TypeScript configuration
- [ ] Product: WhatsApp Business API application submitted (WATI)

**Deliverables**:
- [ ] GitHub repo with CI/CD running green
- [ ] AWS infrastructure accessible
- [ ] Local dev environment documentation

---

#### Week 2: Database Schema & Authentication
**Goals**: Database schema complete, auth framework working

**Tasks**:
- [ ] Backend (Python): PostgreSQL schema design (T1-T7 tables, audit log, cost parameters)
- [ ] Backend (Python): Initial data models, TypeORM or SQLAlchemy entities
- [ ] Backend (Node): JWT middleware, plant-scoped tokens, RBAC (roles: admin, supervisor, qa, owner)
- [ ] Backend (Node): Login/logout endpoints, token refresh logic
- [ ] DevOps: Database backup automation (daily, 30-day retention)
- [ ] DevOps: Secrets management (AWS Secrets Manager for API keys)
- [ ] QA: Authentication test suite (valid/invalid tokens, plant isolation)

**Deliverables**:
- [ ] PostgreSQL schema deployed to staging
- [ ] Auth endpoints tested
- [ ] Backup automation verified

---

#### Week 3: API Skeleton & Offline Storage
**Goals**: Core API endpoints stubbed, offline storage ready

**Tasks**:
- [ ] Backend (Node): REST API skeleton (Fastify routes)
  - POST /api/auth/login
  - GET /api/plants/{plantId}/machines
  - POST /api/plants/{plantId}/shifts
  - POST /api/plants/{plantId}/events
  - GET /api/plants/{plantId}/status
- [ ] Frontend: IndexedDB schema design, sync service skeleton
- [ ] Frontend: Redux store structure (plants, shifts, events, sync state)
- [ ] Frontend: Offline detection (navigator.onLine), sync triggers
- [ ] QA: API contract tests (request/response validation)

**Deliverables**:
- [ ] API endpoints responding (no business logic yet)
- [ ] Frontend offline storage working
- [ ] Contract tests defining expectations

---

#### Week 4: Monitoring & Documentation
**Goals**: Observability in place, team documentation complete

**Tasks**:
- [ ] DevOps: Prometheus + Grafana setup (dashboards for API latency, error rates, database connections)
- [ ] DevOps: Sentry error tracking integration
- [ ] Backend (Node): Logging middleware (Winston or Pino)
- [ ] Backend: API documentation (OpenAPI 3.0 + Swagger UI)
- [ ] Product: Developer onboarding guide, architecture diagrams
- [ ] Product: Plant onboarding process documented (T1-T7 upload flow)
- [ ] Tech Lead: Code review checklist established

**Deliverables**:
- [ ] Monitoring dashboards accessible
- [ ] API docs auto-generated
- [ ] Team can onboard new developers

---

### Phase 2: Master Data & Screens (Weeks 5-12)

#### Week 5-6: Master File Upload
**Goals**: T1-T7 templates parsed, plant master data loaded

**Tasks**:
- [ ] Frontend: File upload component (React Dropzone)
  - Accept Excel (.xlsx), CSV
  - Show preview before upload
  - Validation error reporting
- [ ] Backend: Upload endpoint (POST /api/plants/{plantId}/upload/master-files)
- [ ] Backend (Python): CSV/Excel parsing (pandas, openpyxl)
  - T1: Machines (station_id, type, cost_family, bottleneck)
  - T2: Parts (part_number, customer, family, margin)
  - T3: Cycle times (part_id, operation, cycle_time_minutes)
  - T4: Cost parameters (cost_family, machine_rate, labour_rate, overhead_rate)
  - T5: People (name, role, whatsapp_number, escalation_level)
  - T6: Reason codes (code, description, recoverable_flag)
  - T7: Shifts (shift_name, start_time, end_time, breaks)
- [ ] Backend: Validation engine (uniqueness, data types, ranges)
- [ ] Frontend: Confirmation screen ("Found 132 parts, 19 machines, 41 operators")
- [ ] QA: Upload E2E tests (valid files, error handling, duplicate detection)

**Deliverables**:
- [ ] One pilot plant's master data uploaded & ready
- [ ] Validation errors clearly reported to user
- [ ] Confidence screen showing parsed counts

---

#### Week 7-8: Shift & Job Order Screens
**Goals**: Screens 1-2 working (Shift Start, Machine Grid)

**Tasks**:
- [ ] Frontend (Screen 1 — Shift Start):
  - Dropdown: Select shift (Shift A, Shift B)
  - Date picker: Auto-today
  - Table: Job orders for today (pre-populated from production plan)
    - Part number | Machine | Operator (editable)
  - Button: "Start Shift" → locks for entry
  - Validation: Cannot proceed without confirming at least 1 job
  - Confirmation: "Shift A started at 07:00, 3 jobs loaded"
- [ ] Frontend (Screen 2 — Machine Grid):
  - Grid layout: One tile per machine
  - Tile shows:
    - Machine name + status (GREEN = running, RED = stopped)
    - Current production count vs target (progress bar)
    - Live cost ticker (₹ accumulating if downtime)
    - Time on screen (how long since status changed)
  - Tap machine → Downtime Log screen
  - Real-time updates (WebSocket or polling every 5 seconds)
- [ ] Backend: Machine status endpoint (GET /api/plants/{plantId}/machines/status)
- [ ] Backend: Real-time cost calculation (cached, updated on event)
- [ ] QA: Mobile responsiveness tests (tablets at various sizes)

**Deliverables**:
- [ ] Shift Start → Machine Grid flow working
- [ ] Live status visible on tablet
- [ ] Cost ticker updating in real-time

---

#### Week 9-10: Downtime & Production Entry
**Goals**: Screens 3-4 working (Downtime Log, Production Entry)

**Tasks**:
- [ ] Frontend (Screen 3 — Downtime Log):
  - Machine → tap "Machine Stopped" button
  - Modal: Reason code dropdown (biggest buttons for common codes)
  - Optional note field (free text)
  - Button: "Save" → event created, cost calculated
  - Cancel: Back to grid
  - Timer starts (red background, ₹ climbing)
  - Button: "Running" (when machine restarts) → close event
  - Validation: Reason code mandatory
- [ ] Frontend (Screen 4 — Production Entry):
  - Machine → tap "Log Count"
  - Modal: Number input (count since last entry)
  - Optional timestamp picker (auto = now)
  - Button: "Save" → production count recorded
  - Hourly reminder nudge (push notification)
  - Batch completion shortcut (trigger from job order)
- [ ] Backend: Downtime event creation (POST /api/events/downtime)
- [ ] Backend: Production count creation (POST /api/events/production)
- [ ] Backend: Real-time cost calculation (cost engine triggered)
- [ ] QA: Offline entry + sync (enter while offline, sync when online)

**Deliverables**:
- [ ] Downtime & production data flowing
- [ ] Cost calculated immediately
- [ ] Offline entry working

---

#### Week 11: Quality Entry & Shift Closure
**Goals**: Screens 5-6 working (Quality Entry, Shift Closure)

**Tasks**:
- [ ] Frontend (Screen 5 — Quality Entry):
  - Inspector independent login (different from supervisor)
  - Machine/Part selector
  - Good count input
  - Reject count input
  - Defect code multi-select (checkboxes)
  - Disposition dropdown (scrap / rework / hold)
  - Button: "Submit" → quality record locked
  - No re-entry (shift-level lock)
- [ ] Frontend (Screen 6 — Shift Closure):
  - Checklist:
    - [ ] All downtime events have reason codes
    - [ ] All production entries complete
    - [ ] All quality records submitted
    - [ ] All escalations resolved
  - If any unchecked: show blocking item, mark RED
  - Supervisor can request override (logged, owner approval)
  - Button: "Close Shift" (blocked if checkboxes unchecked)
  - Confirmation: "Shift locked at 15:35. No further edits allowed."
  - Data now read-only
- [ ] Backend: Quality record creation (POST /api/events/quality)
- [ ] Backend: Shift closure endpoint (POST /api/shifts/{shiftId}/close)
  - Validation: All events complete
  - Lock shift (set locked_at timestamp)
  - Trigger end-of-shift report generation
- [ ] QA: Shift closure blocking rules tested (cannot proceed until clean)

**Deliverables**:
- [ ] Full daily entry flow working
- [ ] Shift closure lock preventing editing
- [ ] Data integrity guaranteed

---

#### Week 12: Cost Engine & Owner Reports
**Goals**: Cost calculation working, owner sees first reports

**Tasks**:
- [ ] Backend (Python) — Cost Engine Service:
  - Input: Downtime event + cost parameters
  - Formula 1: Machine cost = (D ÷ 60) × MCR
  - Formula 2: Labour cost = (D ÷ 60) × LCR
  - Formula 3: Overhead cost = (D ÷ 60) × OHR
  - Formula 4: Lost contribution = ⌊D ÷ CT⌋ × CM × BF
  - Formula 5: Total cost = sum above
  - Input: Scrap count + processing cost → scrap cost
  - Output: JSON with breakdown
  - All formulas versioned for audit trail
- [ ] Backend (Python): Bottleneck factor application
  - BF = 1.0 (default + bottleneck machines)
  - BF = 0.5 (non-bottleneck with buffer)
  - Machine/labour/overhead always full cost
- [ ] Backend (Node): End-of-shift report generation
  - Shift summary: Total recoverable loss
  - Ranked losses: Machine → Reason → ₹
  - Split: Recoverable vs Structural
  - Actions created (auto-trigger rules)
- [ ] Backend (Node): WhatsApp delivery integration (mock if WATI not approved yet)
  - Message queue: Report → Redis
  - Job: Format message → send via WATI (or log to file)
- [ ] Frontend (Screen 8 — Owner Money Screen):
  - Four numbers (large, money-first):
    - Recovered Profit (yesterday) — green
    - Lost Profit (yesterday) — red
    - Actions completed (this month) — blue
    - Running ROI (%) — gold
  - Each number tappable → drill to losses/actions
- [ ] QA: Cost calculation verification (test against manually calculated figures from pilot plant)

**Deliverables**:
- [ ] Owner receiving daily reports
- [ ] Cost figures correct
- [ ] Money screen showing key metrics

---

### Phase 3: Intelligence & Integration (Weeks 13-16)

#### Week 13: Escalations & WhatsApp
**Goals**: 5/15/30-minute escalation ladder working

**Tasks**:
- [ ] Backend (Node): Escalation engine
  - Downtime event logged → timer starts
  - 5 min: Push notification to supervisor (mobile only)
  - 15 min: WhatsApp to Maintenance (if breakdown) OR Stores (if material)
  - 30 min: WhatsApp to Plant Head + running ₹ cost
  - All timestamps logged (audit trail)
- [ ] Backend: Escalation rules configurable per plant (thresholds tunable in onboarding)
- [ ] Backend: Message queue (Redis Streams + Bull)
  - Job: Escalation at T=5min
  - Job: Escalation at T=15min
  - Job: Escalation at T=30min
- [ ] Backend: WhatsApp webhook (POST /webhooks/wati/incoming)
  - Receives messages from Copilot queries
  - Routes to Copilot handler
- [ ] QA: Escalation timing (test in staging, verify delays)

**Deliverables**:
- [ ] Escalation ladder flowing
- [ ] WhatsApp integration ready (or SMS fallback if WATI delayed)

---

#### Week 14: Cross-Verification & Actions
**Goals**: Quality vs production reconciliation, action creation rules

**Tasks**:
- [ ] Backend: Cross-verification logic
  - Supervisor hourly production count: e.g., 60 parts
  - QA good count at batch end: e.g., 58 parts
  - Mismatch: |60 - 58| = 2 parts (tolerance ≤ 5)
  - If mismatch > tolerance: Alert owner (auto-escalation)
  - Message: "Line 2 hourly count mismatch: 60 logged, 58 good. Variance: 2 parts."
- [ ] Backend: Action creation rules
  - Trigger 1: Single event >₹2,500 OR >30 min → create Action
  - Trigger 2: Recurring pattern (same reason, same machine, 3+ in 7 shifts) → create pattern Action
  - Trigger 3: Manual (anyone taps "Create Action" on an event)
  - Action fields: Issue, owner, deadline, linked events, ₹ cost
- [ ] Backend: Action lifecycle states
  - open → root_cause_pending → in_progress → monitoring → verified/reopened
- [ ] Frontend (Screen 9 — Actions Board):
  - Kanban board: Open | Root Cause | In Progress | Monitoring | Verified
  - Each card shows:
    - Issue title
    - Owner name
    - Deadline (red if overdue)
    - Linked events count
    - Total ₹ at stake
    - Root cause (if known)
    - Countermeasure (if planned)
  - Tap card → detail view
- [ ] QA: Action trigger rules tested (>₹2,500, recurring pattern, manual)

**Deliverables**:
- [ ] Cross-verification working
- [ ] Actions created automatically
- [ ] Actions board visible

---

#### Week 15: Morning Meeting Mode & Severity Routing
**Goals**: Morning Meeting screen, PDCA triage logic

**Tasks**:
- [ ] Frontend (Screen 7 — Morning Meeting Mode):
  - Big-screen display (5-meter readability)
  - Yesterday's locked losses (ranked by ₹)
  - Top 3 downtime by machine
  - Top 3 quality issues by defect
  - Open actions blocking Today's Plan (overdue items highlighted)
  - Actions gate: Cannot proceed to Today's Plan until escalated actions resolved/delayed-with-reason
  - Workflow: Triage → PDCA → Closure → Next agenda item
- [ ] Backend: Severity routing (morning meeting router)
  - Show all losses (transparency)
  - Escalate only material issues to PDCA/RCA:
    - Severity threshold: >₹2,500 OR >30 min OR quality-escape flag
    - Recurring pattern: 3+ same reason in 7 shifts
    - Manual: Anyone can raise
  - Non-escalated losses: Logged, costed, visible, but no PDCA work assigned
- [ ] Backend: Morning meeting data endpoint (GET /morning-meeting)
  - Returns ranked losses, escalated actions, open blockers
- [ ] QA: Severity routing logic tested (some issues escalated, others logged only)

**Deliverables**:
- [ ] Morning meeting running daily
- [ ] Precision routing reducing RCA fatigue

---

#### Week 16: RCA & Recovered Profit Calculation
**Goals**: Root cause assignment, action closure, savings verification

**Tasks**:
- [ ] Frontend (Screen 10 — RCA & Closure Prompt):
  - When action closing:
  - Option A: Root cause already known → enter root cause + countermeasure
  - Option B: Root cause unknown → assign RCA (to maintenance, to quality, etc.)
    - 5-Why template pre-filled with event history
    - Deadline set
    - RCA handler receives email + WhatsApp notification
  - Option C: Countermeasure executed → document it
  - Photo upload (evidence)
  - Four Factory Memory questions (60 seconds):
    1. What fixed it?
    2. What did you replace/change?
    3. Photo (optional)
    4. What should someone check first next time?
  - Button: "Close Action" → action moves to monitoring phase
- [ ] Backend: RCA assignment (POST /api/actions/{actionId}/assign-rca)
  - Create RCA record + send notification to handler
- [ ] Backend: Recovered Profit calculation
  - Baseline loss: Average weekly ₹ loss for linked reason+machine, 4 weeks before action
  - Post-fix loss: Weekly ₹ loss after action closed
  - Monitoring period: 2 weeks (action state = "monitoring")
  - If loss stayed down (post-fix < baseline for 2 consecutive weeks):
    - Recovered Profit = (baseline - post-fix) × weeks sustained
    - Status → "verified"
    - Savings credited to action owner
  - If loss recurred:
    - Status → "reopened" (not a fix)
    - Recovered Profit = 0
  - Never negative, never counted twice
- [ ] Backend: Factory Memory capture (RCA closure → memory)
  - Store the 4 questions
  - Index by problem category, machine type, part family
  - Searchable (later: via Copilot)
- [ ] QA: Recovered Profit calculation verified (conservative, baseline frozen)

**Deliverables**:
- [ ] RCA assignments flowing
- [ ] Action closures capturing knowledge
- [ ] Recovered Profit correctly computed

---

### Phase 4: AI Intelligence & Copilot (Weeks 17-24)

#### Week 17-18: AI Shift Coach (Rule-Based Tier 1)
**Goals**: 5:45 AM briefing with 3 priorities

**Tasks**:
- [ ] Backend (Python): Coach rule engine
  - Rule 1: Biggest loss yesterday (top loss by machine + reason + ₹)
  - Rule 2: Recurring issues (same reason 3+ times in 5 shifts, no action logged)
  - Rule 3: Overdue actions (deadline passed, status not verified)
  - Rule 4: Bottleneck risk (machine averaging N breakages per K days; today is day K → high risk)
- [ ] Backend: Coach briefing generation (JSON)
  ```json
  {
    "tier": 1,
    "priorities": [
      {
        "rank": 1,
        "machine": "CNC-3",
        "category": "tool_breakage",
        "context": "CNC-3 averages a tool breakage every 4.2 days. Today is Day 4 since the last one. 73% happen in first 90 min.",
        "action": "Inspect and replace insert on Station 3",
        "assigned_to": "Suresh K. (Maintenance)",
        "deadline": "07:15 AM",
        "prevention_value": 3200
      },
      ...
    ]
  }
  ```
- [ ] Backend: Scheduled delivery (5:45 AM daily)
  - Cron job: Generate briefing → queue for delivery
  - Delivery via WhatsApp (same WATI channel as escalations)
- [ ] Frontend: Optional: Coach briefing display on supervisor phone (same as message)
- [ ] QA: Coach rule firing tested (rule conditions validated)

**Deliverables**:
- [ ] Supervisor receiving 3 prioritized actions daily
- [ ] Briefings actionable (named person, deadline, ₹ value)

---

#### Week 19-20: Copilot Factual Tier
**Goals**: WhatsApp question-answering, factual queries

**Tasks**:
- [ ] Backend (Python): Copilot service
  - Input: Question text (English or Tamil)
  - Processing:
    - Parse question intent (time range, machine, metric)
    - Query database for facts
    - Compose natural language answer
    - Include confidence level & data basis
  - Output: Answer text + metadata (tier, confidence, data points)
- [ ] Factual question types (Tier 1):
  - "How much did we lose this week?" → Recoverable loss, trend
  - "Which machine lost the most?" → Machine name + cost
  - "What happened on [machine] today?" → Events + costs
  - "How many defects did we have?" → Rejection count + top defects
- [ ] Backend: Copilot API endpoint (POST /api/copilot/ask)
- [ ] Backend: WhatsApp webhook handler
  - Receives question from supervisor
  - Forwards to Copilot service
  - Streams response back via WATI
  - All questions logged (audit trail)
- [ ] Frontend: Optional: Copilot chat UI in-app (for web, not MVP)
- [ ] QA: Copilot accuracy tested (sample questions, verify answers against database)

**Deliverables**:
- [ ] Supervisor can ask questions via WhatsApp
- [ ] Factual answers flowing back

---

#### Week 21-22: Copilot Pattern Tier
**Goals**: Unlock pattern answers after history accumulation

**Tasks**:
- [ ] Backend: History accumulation logic
  - Day 1-10: Tier 0 (silent, no coaching)
  - 10+ shifts: Tier 1 (rule-based + factual)
  - 4+ weeks (20+ shifts): Tier 2 (pattern answers unlocked)
  - 12+ weeks: Tier 3 (deep correlations)
- [ ] Backend: Pattern detection (Tier 2)
  - Breakage intervals: "CNC-3 breaks every X days" (MTBF calculation)
  - Time-of-shift patterns: "70% of breakages happen first 90 minutes"
  - Defect trends: "Burr defects increased from 0.8% → 2.1% last 3 weeks"
  - Root causes: "Last 3 tool breakages were all bearing-related"
- [ ] Backend: Copilot pattern questions
  - "Why does CNC-3 keep failing?"
  - "What usually fixes this defect?"
  - "Which defect is increasing?" → machine + trend + ₹ impact
- [ ] Backend: Confidence self-limiting
  - If pattern has <5 data points: "I've seen only 3 events; pattern analysis unlocks after 30+ days"
  - Always state data basis: "Based on 47 events over 42 days"
- [ ] QA: Pattern detection accuracy (compare to manual analysis)

**Deliverables**:
- [ ] Pattern tier unlocking after sufficient history
- [ ] Copilot self-limiting confidently

---

#### Week 23-24: Coach Tier 2 & Integration Cleanup
**Goals**: Coach deepening, all systems integration tested

**Tasks**:
- [ ] Backend: Coach Tier 2 (4-6 weeks of data)
  - Breakage intervals: "Every 4.2 days" (MTBF-based)
  - Risk windows: "73% happen first 90 minutes" (time-of-shift pattern)
  - Prevention actions: Named person, deadline, ₹ value
  - Example: "CNC-3: High tool-breakage risk today. Last one was 4 days ago. Inspect + replace insert, Suresh K., 07:15, ₹3,200 saved."
- [ ] Backend: Cron job monitoring (coach delivery, report generation, action verification)
- [ ] Backend: Cost formula versioning (track all changes)
- [ ] Backend: Audit log queries (compliance: retrieve any event's before/after state)
- [ ] Frontend: PWA installation (add-to-home screen working)
- [ ] Frontend: Offline sync verification (tablet offline, enter data, sync when online)
- [ ] QA: Integration testing (end-to-end: shift start → cost → escalation → action → verified savings)
- [ ] QA: Mobile device testing (iPad, Android tablets, actual devices if possible)
- [ ] Product: Pilot plant training (supervisor + maintenance + owner)

**Deliverables**:
- [ ] Coach Tier 2 generating valuable predictions
- [ ] All systems integrated
- [ ] Pilot plant ready for live use

---

### Phase 5: Parallel — Plugins (Weeks 9-24)

#### Compliance/Audit AI Plugin (Weeks 9-20)

**Tasks**:
- Week 9-10: Compliance data model design (clauses, CAPA mappings)
- Week 11-12: ISO 9001 / IATF 16949 / ZED clause mapping
- Week 13-14: Evidence generation from Core events
  - Downtime log + action closure = preventive action evidence
  - Quality record = traceability + CAPA evidence
- Week 15-16: Audit readiness scoring + compliance dashboard
- Week 17-18: CAPA assignment flow (Actions engine integration)
- Week 19-20: Reports & compliance export (for OEM audits)

**Key Constraint**: Reads Core data only, writes Actions only

---

#### Kaizen AI Plugin (Weeks 9-20)

**Tasks**:
- Week 9-10: Idea submission UX (form + eventual voice recording)
- Week 11-12: Idea-to-proposal conversion (cost savings structured)
- Week 13-14: Idea scoring & prioritization logic
- Week 15-16: Kaizen Ideas → Actions routing (same Actions engine)
- Week 17-18: Realized savings tracking + idea owner incentives
- Week 19-20: Recognition dashboard

**Key Constraint**: Reads Core data only, writes Actions & metrics only

---

#### Skill Matrix AI Plugin (Weeks 9-20)

**Tasks**:
- Week 9-10: Training matrix data model (who trained on what)
- Week 11-12: "No Operator" downtime → skill gap mapping
- Week 13-14: Training needs ranked by ₹ loss prevented
- Week 15-16: Training completion tracking
- Week 17-18: Metrics dashboard (reduction in "No Operator" events)
- Week 19-20: Integration with HR (training schedules)

**Key Constraint**: Reads Core data only, writes Actions only

---

## Part 3: Critical Integration Points & Testing

### 3.1 Integration Testing Checklist

```
Core Data Entry → Cost Engine → Escalations → Owner Report
├─ Supervisor logs downtime "Tool Breakage" CNC-3, 47 min
├─ Cost calculated: Machine (327) + Labour (235) + Overhead (141) + Lost Contribution (2587) = 3290
├─ Live ticker updates on Machine Grid
├─ 5-min alert to supervisor
├─ 15-min WhatsApp to Maintenance
├─ 30-min alert to Plant Head (running ₹)
├─ Event locked at shift close
├─ Owner receives report: "CNC-3 tool breakage: ₹3,290"
└─ Action auto-created if recurring (3+ pattern)

Cross-Verification → Alerts
├─ Supervisor logs 60 parts/hour
├─ QA logs 58 good, 2 rejected at batch
├─ Mismatch: 60 vs 58 = 2 (within tolerance, no alert)
├─ If mismatch 60 vs 50 = 10 (beyond tolerance)
│  └─ Alert to owner: "Line 2 count mismatch: 60 logged, 50 verified. Investigate"
└─ Investigation Action created

Action Lifecycle → Recovered Profit
├─ Action created: "Material wait Line 2" ₹4,400/week baseline (4-week average)
├─ Root cause found: Short staging procedure
├─ Countermeasure: Material staging 30 min before shift
├─ Implemented, monitoring starts
├─ Week 1 post-fix: ₹1,100 loss (still monitoring)
├─ Week 2 post-fix: ₹1,050 loss (2 weeks sustained, 2-week rule met)
├─ Action verified: Recovered Profit = ₹4,400 - ₹1,050 = ₹3,350/week
├─ Annualized: ₹3,350 × 52 = ₹1.74 Lakh
└─ Owner home screen: "Month 1 Recovered Profit: ₹1.3L"

Offline Sync → Conflict Resolution
├─ Tablet 1: Downtime event logged at 10:14 (offline)
├─ Tablet 2: Downtime event logged at 10:16 (online, synced)
├─ Tablet 1: Later syncs (internet returns)
├─ Server: Both events valid (different machines, no conflict)
├─ Resolution: Both recorded, chronologically ordered
├─ Audit trail: Sync log shows order of arrival
└─ Owner report: Both costs included, sorted by timestamp
```

### 3.2 Mobile Device Testing

**Devices to test**:
- iPad (iOS 15+, 10-inch)
- Samsung Tab S6 Lite (Android 12, 10-inch)
- iPhone 12 (iOS 15+)
- Realme 10 Pro (Android 13, 6-inch)

**Test scenarios**:
- Offline entry for 30 min, then sync
- Tablet sleeping & waking up (battery management)
- Poor WiFi (simulated with network throttling)
- Device rotation (landscape ↔ portrait)
- Thick-finger taps (5mm offset tolerance)
- Low light readability

---

## Part 4: Go-Live Checklist (Week 24)

Before first plant launch:

- [ ] All 6 core screens working on tablet
- [ ] Cost figures verified (test against manual calculations)
- [ ] Offline sync working (tested on devices)
- [ ] WhatsApp delivery confirmed (with real WATI account)
- [ ] Shift closure lock blocking editing
- [ ] Owner reports flowing daily
- [ ] Actions created correctly (trigger rules verified)
- [ ] Morning meeting triage logic working (only material issues escalated)
- [ ] Recovered Profit calculation verified (baseline frozen, 2-week sustain)
- [ ] Audit trail immutable (no retroactive edits possible)
- [ ] Pilot plant supervisor trained (shift entry, cost review, action closure)
- [ ] Pilot plant owner trained (reading reports, approving actions)
- [ ] Plant data backed up (daily, 30-day retention)
- [ ] Monitoring dashboards live (latency, error rates, uptime)
- [ ] Support contacts established (on-call rotation)
- [ ] Rollback plan ready (database snapshot, previous app version)

---

## Part 5: Post-Launch Roadmap

### Weeks 25-32: Stage 3 & Plugins Deep Dive

- [ ] Coach Tier 3: Deep pattern correlations (PM gaps → breakdowns)
- [ ] Copilot: Supervisor question set expansion
- [ ] Compliance plugin: Live compliance scoring
- [ ] Kaizen plugin: Operator idea voice recording (Tamil support)
- [ ] Skill Matrix plugin: HR integration (training schedules)

### Weeks 33-40: Hardware Phase Planning

- [ ] IoT device selection (CT clamps, controller taps, pulse counters per machine class)
- [ ] Sensor data contract finalization (same EventIngestionLayer)
- [ ] Pilot hardware integration (2-3 machines on Pilot Plant)
- [ ] Automatic cycle counting testing
- [ ] Automatic downtime detection testing

### Weeks 41-52: Network & Benchmark

- [ ] 2nd, 3rd, 4th plant onboarding
- [ ] Digital Readiness Questionnaire refined (score prediction)
- [ ] Onboarding consultant workflow optimized
- [ ] Benchmark data collection (anonymized metrics)
- [ ] Benchmark dashboard design

---

## Part 6: Success Metrics

### Technical KPIs

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Shift entry time** | <2 min (supervisor) | Logged in analytics |
| **Cost calculation latency** | <1 sec | Server logs, latency dashboard |
| **Offline sync time** | <5 sec (when online) | Sync log analysis |
| **WhatsApp delivery** | <30 sec (from event to message) | WATI API response logs |
| **API uptime** | 99.9% | Monitoring dashboard |
| **Error rate** | <0.1% | Sentry dashboard |

### Business KPIs

| Metric | Target (Month 1) | Target (Month 3) |
|--------|-----------------|-----------------|
| **Recoverable loss identified** | ₹40-80K per plant | ₹150-250K per plant |
| **Actions created** | 8-12 per plant | 20-30 per plant |
| **Actions verified** | 2-3 per plant | 6-10 per plant |
| **Recovered Profit** | ₹5-10K per plant | ₹50-100K per plant |
| **Owner satisfaction** | 80%+ (survey) | 90%+ (NPS >50) |
| **Data entry adoption** | 85%+ (shifts on-time closure) | 95%+ |

---

**Document Complete**. All phases, decisions, and integration points documented. Ready for technical team to begin Week 1 infrastructure setup.

