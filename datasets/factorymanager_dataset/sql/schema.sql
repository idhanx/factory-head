-- FactoryManager AI schema at Alembic head 0ba4b6bb4343, dumped from a database built by
--   alembic upgrade head   (plain PostgreSQL 16, no TimescaleDB: hypertables are an optional optimisation)
-- Load this first into an empty database, then factorymanager_data.sql.

--
-- PostgreSQL database dump
--

-- Dumped from database version 16.6
-- Dumped by pg_dump version 16.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: action_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.action_events (
    id uuid NOT NULL,
    action_id uuid NOT NULL,
    downtime_event_id uuid NOT NULL,
    plant_id uuid NOT NULL
);


--
-- Name: actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.actions (
    id uuid NOT NULL,
    reference character varying(32) NOT NULL,
    title character varying(300) NOT NULL,
    description text,
    trigger character varying(24) NOT NULL,
    trigger_rationale text,
    state character varying(16) NOT NULL,
    station_id uuid,
    reason_code_id uuid,
    linked_cost bigint NOT NULL,
    occurrence_count integer NOT NULL,
    routed_to_rca boolean NOT NULL,
    severity_reasons jsonb,
    owner_id uuid,
    owner_name character varying(200),
    due_date date,
    root_cause text,
    countermeasure text,
    evidence_photo_url character varying(500),
    closed_at timestamp with time zone,
    verified_at timestamp with time zone,
    reopened_at timestamp with time zone,
    reopen_count integer NOT NULL,
    baseline_weekly_loss bigint NOT NULL,
    baseline_frozen_at timestamp with time zone,
    recovered_profit bigint NOT NULL,
    sustained_weeks integer NOT NULL,
    surfaced_in_meeting_on date,
    meeting_status character varying(16),
    delay_reason text,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: alert_recipients; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_recipients (
    id uuid NOT NULL,
    name character varying(200) NOT NULL,
    role_tag character varying(32) NOT NULL,
    whatsapp_number character varying(20),
    escalation_level integer NOT NULL,
    user_id uuid,
    is_active boolean NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id uuid NOT NULL,
    plant_id uuid NOT NULL,
    actor_id uuid,
    entity_type character varying(64) NOT NULL,
    entity_id uuid,
    action character varying(32) NOT NULL,
    before jsonb,
    after jsonb,
    reason text,
    occurred_at timestamp with time zone NOT NULL
);


--
-- Name: audit_packs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_packs (
    id uuid NOT NULL,
    standard character varying(20) NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    readiness_score integer NOT NULL,
    clause_summary jsonb,
    evidence jsonb,
    generated_by_id uuid,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: coach_briefings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.coach_briefings (
    id uuid NOT NULL,
    business_date date NOT NULL,
    shift_id uuid,
    recipient_id uuid,
    tier integer NOT NULL,
    data_basis jsonb,
    priorities jsonb,
    fact_bundle jsonb,
    rendered_text text,
    generated_by character varying(16) NOT NULL,
    delivered_at timestamp with time zone,
    opened_at timestamp with time zone,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: compliance_clauses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.compliance_clauses (
    id uuid NOT NULL,
    standard character varying(20) NOT NULL,
    clause_ref character varying(32) NOT NULL,
    title character varying(300) NOT NULL,
    requirement text,
    evidence_source character varying(64) NOT NULL,
    status character varying(20) NOT NULL,
    evidence_count integer NOT NULL,
    last_evidence_at timestamp with time zone,
    gap_note text,
    also_satisfies character varying(64)[],
    display_order integer NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: copilot_queries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.copilot_queries (
    id uuid NOT NULL,
    user_id uuid,
    channel character varying(16) NOT NULL,
    question text NOT NULL,
    language character varying(8) NOT NULL,
    intent character varying(64),
    tier_required character varying(16) NOT NULL,
    tier_available boolean NOT NULL,
    fact_bundle jsonb,
    answer text,
    answered_by character varying(16) NOT NULL,
    latency_ms integer,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cost_families; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cost_families (
    id uuid NOT NULL,
    code character varying(64) NOT NULL,
    name character varying(200) NOT NULL,
    description text,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cost_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cost_rates (
    id uuid NOT NULL,
    scope character varying(20) NOT NULL,
    scope_ref character varying(64) NOT NULL,
    machine_rate bigint NOT NULL,
    labour_rate bigint NOT NULL,
    overhead_rate bigint NOT NULL,
    source character varying(16) NOT NULL,
    derivation jsonb,
    effective_from date NOT NULL,
    effective_to date,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: defect_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.defect_codes (
    id uuid NOT NULL,
    code character varying(64) NOT NULL,
    label character varying(200) NOT NULL,
    label_ta character varying(200),
    part_family character varying(100),
    display_order integer NOT NULL,
    is_active boolean NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dispatch_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dispatch_records (
    id uuid NOT NULL,
    lot_id uuid NOT NULL,
    business_date date NOT NULL,
    quantity integer NOT NULL,
    vehicle_reference character varying(128),
    despatched_at timestamp with time zone NOT NULL,
    source character varying(12) NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: downtime_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.downtime_events (
    id uuid NOT NULL,
    station_id uuid NOT NULL,
    job_session_id uuid,
    shift_session_id uuid,
    business_date date NOT NULL,
    reason_code_id uuid,
    started_at timestamp with time zone NOT NULL,
    ended_at timestamp with time zone,
    duration_minutes numeric(12,4),
    status character varying(12) NOT NULL,
    note text,
    source character varying(12) NOT NULL,
    source_ref character varying(128),
    logged_by_id uuid,
    escalation_level integer NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    cost_total bigint NOT NULL,
    cost_breakdown jsonb,
    snapshot_machine_rate bigint NOT NULL,
    snapshot_labour_rate bigint NOT NULL,
    snapshot_overhead_rate bigint NOT NULL,
    snapshot_cycle_time_min numeric(10,4),
    snapshot_contribution_margin bigint NOT NULL,
    snapshot_bottleneck_factor numeric(3,2) NOT NULL,
    snapshot_costing_mode character varying(16) NOT NULL,
    formula_version integer NOT NULL,
    CONSTRAINT ck_downtime_time_order CHECK (((ended_at IS NULL) OR (ended_at >= started_at)))
);


--
-- Name: escalations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.escalations (
    id uuid NOT NULL,
    downtime_event_id uuid NOT NULL,
    level integer NOT NULL,
    due_at timestamp with time zone NOT NULL,
    sent_at timestamp with time zone,
    channel character varying(12) NOT NULL,
    status character varying(12) NOT NULL,
    recipient_name character varying(200),
    recipient_number character varying(20),
    message text,
    cost_at_send bigint NOT NULL,
    provider_response jsonb,
    acknowledged_at timestamp with time zone,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: job_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.job_orders (
    id uuid NOT NULL,
    job_number character varying(64) NOT NULL,
    part_id uuid NOT NULL,
    customer_name character varying(200),
    planned_quantity integer NOT NULL,
    status character varying(16) NOT NULL,
    due_date date,
    opened_at timestamp with time zone,
    completed_at timestamp with time zone,
    notes text,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: job_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.job_sessions (
    id uuid NOT NULL,
    job_order_id uuid NOT NULL,
    shift_session_id uuid NOT NULL,
    station_id uuid NOT NULL,
    lot_id uuid,
    routing_operation_id uuid,
    operator_id uuid,
    started_at timestamp with time zone NOT NULL,
    ended_at timestamp with time zone,
    ended_by_change_job boolean NOT NULL,
    previous_session_id uuid,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: kaizen_ideas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kaizen_ideas (
    id uuid NOT NULL,
    reference character varying(32) NOT NULL,
    title character varying(300) NOT NULL,
    description text NOT NULL,
    submitted_by_name character varying(200) NOT NULL,
    submitted_by_operator_id uuid,
    station_id uuid,
    reason_code_id uuid,
    status character varying(20) NOT NULL,
    estimated_annual_saving bigint NOT NULL,
    effort_score integer NOT NULL,
    impact_score integer NOT NULL,
    score_rationale text,
    linked_loss_paise bigint NOT NULL,
    action_id uuid,
    decided_at timestamp with time zone,
    decision_note text,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: lots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lots (
    id uuid NOT NULL,
    lot_code character varying(64) NOT NULL,
    job_order_id uuid,
    granularity character varying(16) NOT NULL,
    quantity integer NOT NULL,
    opened_at timestamp with time zone,
    closed_at timestamp with time zone,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: margin_band_values; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.margin_band_values (
    id uuid NOT NULL,
    part_family character varying(100) NOT NULL,
    band character varying(2) NOT NULL,
    value bigint NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: memory_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_entries (
    id uuid NOT NULL,
    action_id uuid,
    station_id uuid,
    part_id uuid,
    reason_code_id uuid,
    what_fixed_it text NOT NULL,
    what_was_replaced text,
    photo_url character varying(500),
    check_first_next_time text,
    problem_summary text,
    tags character varying(64)[],
    fixed_by_name character varying(200),
    occurred_on date,
    embedding_status character varying(16) NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: operator_skills; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operator_skills (
    id uuid NOT NULL,
    operator_id uuid NOT NULL,
    station_id uuid NOT NULL,
    level integer NOT NULL,
    certified_on date,
    expires_on date,
    note text,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: operators; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operators (
    id uuid NOT NULL,
    name character varying(200) NOT NULL,
    employee_code character varying(64),
    certified_station_ids uuid[],
    is_active boolean NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: parts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parts (
    id uuid NOT NULL,
    part_number character varying(64) NOT NULL,
    description character varying(255),
    customer_name character varying(200),
    part_family character varying(100),
    contribution_margin bigint,
    margin_band character varying(2),
    material_cost bigint NOT NULL,
    is_active boolean NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: plants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plants (
    id uuid NOT NULL,
    name character varying(200) NOT NULL,
    code character varying(32) NOT NULL,
    location character varying(200),
    timezone character varying(64) NOT NULL,
    currency character varying(8) NOT NULL,
    costing_mode character varying(16) NOT NULL,
    business_date_from_shift_start boolean NOT NULL,
    action_cost_threshold bigint NOT NULL,
    action_duration_minutes bigint NOT NULL,
    recurrence_count bigint NOT NULL,
    recurrence_window_shifts bigint NOT NULL,
    reconciliation_tolerance_parts bigint NOT NULL,
    reconciliation_tolerance_bp bigint NOT NULL,
    no_blame_until date,
    go_live_date date,
    monthly_fee bigint NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: production_counts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.production_counts (
    id uuid NOT NULL,
    station_id uuid NOT NULL,
    job_session_id uuid,
    shift_session_id uuid,
    business_date date NOT NULL,
    count integer NOT NULL,
    recorded_at timestamp with time zone NOT NULL,
    covers_from timestamp with time zone,
    source character varying(12) NOT NULL,
    source_ref character varying(128),
    logged_by_id uuid,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_production_count_nonneg CHECK ((count >= 0))
);


--
-- Name: production_targets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.production_targets (
    id uuid NOT NULL,
    line character varying(64),
    station_id uuid,
    shift_id uuid,
    target_parts integer NOT NULL,
    effective_from date NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: quality_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quality_records (
    id uuid NOT NULL,
    station_id uuid,
    job_session_id uuid,
    shift_session_id uuid,
    lot_id uuid,
    part_id uuid NOT NULL,
    business_date date NOT NULL,
    good_count integer NOT NULL,
    reject_count integer NOT NULL,
    rework_count integer NOT NULL,
    rework_minutes numeric(10,2),
    disposition character varying(12) NOT NULL,
    defects jsonb,
    recorded_at timestamp with time zone NOT NULL,
    source character varying(12) NOT NULL,
    inspector_id uuid,
    reconciliation_gap integer,
    reconciliation_flagged boolean NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    cost_total bigint NOT NULL,
    cost_breakdown jsonb,
    snapshot_machine_rate bigint NOT NULL,
    snapshot_labour_rate bigint NOT NULL,
    snapshot_overhead_rate bigint NOT NULL,
    snapshot_cycle_time_min numeric(10,4),
    snapshot_contribution_margin bigint NOT NULL,
    snapshot_bottleneck_factor numeric(3,2) NOT NULL,
    snapshot_costing_mode character varying(16) NOT NULL,
    formula_version integer NOT NULL,
    CONSTRAINT ck_quality_nonneg CHECK (((good_count >= 0) AND (reject_count >= 0))),
    CONSTRAINT ck_quality_scrap_xor_rework CHECK ((NOT (((disposition)::text = 'scrap'::text) AND (rework_count > 0))))
);


--
-- Name: readiness_assessments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.readiness_assessments (
    id uuid NOT NULL,
    plant_id uuid,
    plant_name character varying(200),
    respondent_name character varying(200),
    respondent_phone character varying(20),
    answers jsonb,
    score integer NOT NULL,
    band character varying(32),
    priority_gaps jsonb,
    estimated_onboarding_hours integer,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: reason_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reason_codes (
    id uuid NOT NULL,
    code character varying(64) NOT NULL,
    label character varying(200) NOT NULL,
    label_ta character varying(200),
    recoverability character varying(2) NOT NULL,
    oee_treatment character varying(16) NOT NULL,
    is_breakdown boolean NOT NULL,
    escalation_route character varying(20) NOT NULL,
    safety_flag boolean NOT NULL,
    display_order integer NOT NULL,
    is_active boolean NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: recovered_profit_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.recovered_profit_entries (
    id uuid NOT NULL,
    action_id uuid NOT NULL,
    week_start date NOT NULL,
    baseline_weekly_loss bigint NOT NULL,
    actual_weekly_loss bigint NOT NULL,
    saving bigint NOT NULL,
    counted boolean NOT NULL,
    note text,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: root_cause_analyses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.root_cause_analyses (
    id uuid NOT NULL,
    action_id uuid NOT NULL,
    assigned_to_id uuid,
    assigned_team character varying(64),
    due_date date,
    five_whys jsonb,
    prefilled_context jsonb,
    ai_suggested_causes jsonb,
    ai_suggestion_accepted boolean,
    conclusion text,
    completed_at timestamp with time zone,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: routing_operations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.routing_operations (
    id uuid NOT NULL,
    part_id uuid NOT NULL,
    sequence integer NOT NULL,
    operation_code character varying(32) NOT NULL,
    name character varying(200),
    section character varying(20) NOT NULL,
    cost_family_id uuid,
    cycle_time_min numeric(10,4),
    cycle_time_source character varying(16) NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: shift_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shift_sessions (
    id uuid NOT NULL,
    shift_id uuid NOT NULL,
    business_date date NOT NULL,
    started_at timestamp with time zone NOT NULL,
    ended_at timestamp with time zone,
    supervisor_id uuid,
    status character varying(16) NOT NULL,
    locked_at timestamp with time zone,
    closed_by_id uuid,
    override_reason text,
    override_by_id uuid,
    planned_minutes integer NOT NULL,
    summary jsonb,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: shifts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shifts (
    id uuid NOT NULL,
    name character varying(64) NOT NULL,
    start_time time without time zone NOT NULL,
    end_time time without time zone NOT NULL,
    break_minutes integer NOT NULL,
    weekly_off character varying(32),
    display_order integer NOT NULL,
    is_active boolean NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: stage_completions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stage_completions (
    id uuid NOT NULL,
    lot_id uuid NOT NULL,
    routing_operation_id uuid NOT NULL,
    station_id uuid,
    business_date date NOT NULL,
    quantity_completed integer NOT NULL,
    quantity_rejected integer NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone NOT NULL,
    source character varying(12) NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: stations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stations (
    id uuid NOT NULL,
    code character varying(64) NOT NULL,
    name character varying(200) NOT NULL,
    station_type character varying(100),
    section character varying(20) NOT NULL,
    is_bottleneck boolean NOT NULL,
    bottleneck_factor numeric(3,2) NOT NULL,
    cost_family_id uuid,
    line character varying(64),
    zone character varying(64),
    is_active boolean NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: template_uploads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.template_uploads (
    id uuid NOT NULL,
    template character varying(8) NOT NULL,
    filename character varying(300) NOT NULL,
    storage_url character varying(500),
    uploaded_by_id uuid,
    status character varying(16) NOT NULL,
    rows_found integer NOT NULL,
    rows_imported integer NOT NULL,
    preview jsonb,
    errors jsonb,
    confirmed_at timestamp with time zone,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: training_needs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.training_needs (
    id uuid NOT NULL,
    station_id uuid NOT NULL,
    operator_id uuid,
    kind character varying(32) NOT NULL,
    annual_loss_paise bigint NOT NULL,
    evidence jsonb,
    recommendation text,
    action_id uuid,
    resolved boolean NOT NULL,
    plant_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_plant_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_plant_roles (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    plant_id uuid NOT NULL,
    role character varying(20) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid NOT NULL,
    email character varying(255),
    phone character varying(20),
    full_name character varying(200) NOT NULL,
    password_hash text NOT NULL,
    preferred_language character varying(8) NOT NULL,
    is_active boolean NOT NULL,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: action_events action_events_action_id_downtime_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_events
    ADD CONSTRAINT action_events_action_id_downtime_event_id_key UNIQUE (action_id, downtime_event_id);


--
-- Name: action_events action_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_events
    ADD CONSTRAINT action_events_pkey PRIMARY KEY (id);


--
-- Name: actions actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actions
    ADD CONSTRAINT actions_pkey PRIMARY KEY (id);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: alert_recipients alert_recipients_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_recipients
    ADD CONSTRAINT alert_recipients_pkey PRIMARY KEY (id);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: audit_packs audit_packs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_packs
    ADD CONSTRAINT audit_packs_pkey PRIMARY KEY (id);


--
-- Name: coach_briefings coach_briefings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coach_briefings
    ADD CONSTRAINT coach_briefings_pkey PRIMARY KEY (id);


--
-- Name: compliance_clauses compliance_clauses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compliance_clauses
    ADD CONSTRAINT compliance_clauses_pkey PRIMARY KEY (id);


--
-- Name: compliance_clauses compliance_clauses_plant_id_standard_clause_ref_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compliance_clauses
    ADD CONSTRAINT compliance_clauses_plant_id_standard_clause_ref_key UNIQUE (plant_id, standard, clause_ref);


--
-- Name: copilot_queries copilot_queries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.copilot_queries
    ADD CONSTRAINT copilot_queries_pkey PRIMARY KEY (id);


--
-- Name: cost_families cost_families_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_families
    ADD CONSTRAINT cost_families_pkey PRIMARY KEY (id);


--
-- Name: cost_families cost_families_plant_id_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_families
    ADD CONSTRAINT cost_families_plant_id_code_key UNIQUE (plant_id, code);


--
-- Name: cost_rates cost_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_rates
    ADD CONSTRAINT cost_rates_pkey PRIMARY KEY (id);


--
-- Name: defect_codes defect_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.defect_codes
    ADD CONSTRAINT defect_codes_pkey PRIMARY KEY (id);


--
-- Name: defect_codes defect_codes_plant_id_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.defect_codes
    ADD CONSTRAINT defect_codes_plant_id_code_key UNIQUE (plant_id, code);


--
-- Name: dispatch_records dispatch_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_records
    ADD CONSTRAINT dispatch_records_pkey PRIMARY KEY (id);


--
-- Name: downtime_events downtime_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.downtime_events
    ADD CONSTRAINT downtime_events_pkey PRIMARY KEY (id);


--
-- Name: escalations escalations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.escalations
    ADD CONSTRAINT escalations_pkey PRIMARY KEY (id);


--
-- Name: job_orders job_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_orders
    ADD CONSTRAINT job_orders_pkey PRIMARY KEY (id);


--
-- Name: job_orders job_orders_plant_id_job_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_orders
    ADD CONSTRAINT job_orders_plant_id_job_number_key UNIQUE (plant_id, job_number);


--
-- Name: job_sessions job_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_sessions
    ADD CONSTRAINT job_sessions_pkey PRIMARY KEY (id);


--
-- Name: kaizen_ideas kaizen_ideas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kaizen_ideas
    ADD CONSTRAINT kaizen_ideas_pkey PRIMARY KEY (id);


--
-- Name: lots lots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lots
    ADD CONSTRAINT lots_pkey PRIMARY KEY (id);


--
-- Name: lots lots_plant_id_lot_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lots
    ADD CONSTRAINT lots_plant_id_lot_code_key UNIQUE (plant_id, lot_code);


--
-- Name: margin_band_values margin_band_values_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.margin_band_values
    ADD CONSTRAINT margin_band_values_pkey PRIMARY KEY (id);


--
-- Name: margin_band_values margin_band_values_plant_id_part_family_band_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.margin_band_values
    ADD CONSTRAINT margin_band_values_plant_id_part_family_band_key UNIQUE (plant_id, part_family, band);


--
-- Name: memory_entries memory_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_entries
    ADD CONSTRAINT memory_entries_pkey PRIMARY KEY (id);


--
-- Name: operator_skills operator_skills_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_skills
    ADD CONSTRAINT operator_skills_pkey PRIMARY KEY (id);


--
-- Name: operator_skills operator_skills_plant_id_operator_id_station_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_skills
    ADD CONSTRAINT operator_skills_plant_id_operator_id_station_id_key UNIQUE (plant_id, operator_id, station_id);


--
-- Name: operators operators_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operators
    ADD CONSTRAINT operators_pkey PRIMARY KEY (id);


--
-- Name: operators operators_plant_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operators
    ADD CONSTRAINT operators_plant_id_name_key UNIQUE (plant_id, name);


--
-- Name: parts parts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts
    ADD CONSTRAINT parts_pkey PRIMARY KEY (id);


--
-- Name: parts parts_plant_id_part_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts
    ADD CONSTRAINT parts_plant_id_part_number_key UNIQUE (plant_id, part_number);


--
-- Name: plants plants_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plants
    ADD CONSTRAINT plants_code_key UNIQUE (code);


--
-- Name: plants plants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plants
    ADD CONSTRAINT plants_pkey PRIMARY KEY (id);


--
-- Name: production_counts production_counts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_counts
    ADD CONSTRAINT production_counts_pkey PRIMARY KEY (id);


--
-- Name: production_targets production_targets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_targets
    ADD CONSTRAINT production_targets_pkey PRIMARY KEY (id);


--
-- Name: quality_records quality_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_records
    ADD CONSTRAINT quality_records_pkey PRIMARY KEY (id);


--
-- Name: readiness_assessments readiness_assessments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.readiness_assessments
    ADD CONSTRAINT readiness_assessments_pkey PRIMARY KEY (id);


--
-- Name: reason_codes reason_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reason_codes
    ADD CONSTRAINT reason_codes_pkey PRIMARY KEY (id);


--
-- Name: reason_codes reason_codes_plant_id_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reason_codes
    ADD CONSTRAINT reason_codes_plant_id_code_key UNIQUE (plant_id, code);


--
-- Name: recovered_profit_entries recovered_profit_entries_action_id_week_start_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recovered_profit_entries
    ADD CONSTRAINT recovered_profit_entries_action_id_week_start_key UNIQUE (action_id, week_start);


--
-- Name: recovered_profit_entries recovered_profit_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recovered_profit_entries
    ADD CONSTRAINT recovered_profit_entries_pkey PRIMARY KEY (id);


--
-- Name: root_cause_analyses root_cause_analyses_action_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.root_cause_analyses
    ADD CONSTRAINT root_cause_analyses_action_id_key UNIQUE (action_id);


--
-- Name: root_cause_analyses root_cause_analyses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.root_cause_analyses
    ADD CONSTRAINT root_cause_analyses_pkey PRIMARY KEY (id);


--
-- Name: routing_operations routing_operations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routing_operations
    ADD CONSTRAINT routing_operations_pkey PRIMARY KEY (id);


--
-- Name: routing_operations routing_operations_plant_id_part_id_sequence_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routing_operations
    ADD CONSTRAINT routing_operations_plant_id_part_id_sequence_key UNIQUE (plant_id, part_id, sequence);


--
-- Name: shift_sessions shift_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_sessions
    ADD CONSTRAINT shift_sessions_pkey PRIMARY KEY (id);


--
-- Name: shift_sessions shift_sessions_plant_id_shift_id_business_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_sessions
    ADD CONSTRAINT shift_sessions_plant_id_shift_id_business_date_key UNIQUE (plant_id, shift_id, business_date);


--
-- Name: shifts shifts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT shifts_pkey PRIMARY KEY (id);


--
-- Name: shifts shifts_plant_id_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT shifts_plant_id_name_key UNIQUE (plant_id, name);


--
-- Name: stage_completions stage_completions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stage_completions
    ADD CONSTRAINT stage_completions_pkey PRIMARY KEY (id);


--
-- Name: stations stations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT stations_pkey PRIMARY KEY (id);


--
-- Name: stations stations_plant_id_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT stations_plant_id_code_key UNIQUE (plant_id, code);


--
-- Name: template_uploads template_uploads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.template_uploads
    ADD CONSTRAINT template_uploads_pkey PRIMARY KEY (id);


--
-- Name: training_needs training_needs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_needs
    ADD CONSTRAINT training_needs_pkey PRIMARY KEY (id);


--
-- Name: user_plant_roles user_plant_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_plant_roles
    ADD CONSTRAINT user_plant_roles_pkey PRIMARY KEY (id);


--
-- Name: user_plant_roles user_plant_roles_user_id_plant_id_role_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_plant_roles
    ADD CONSTRAINT user_plant_roles_user_id_plant_id_role_key UNIQUE (user_id, plant_id, role);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_phone_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_phone_key UNIQUE (phone);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: ix_action_events_action_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_events_action_id ON public.action_events USING btree (action_id);


--
-- Name: ix_action_events_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_action_events_plant_id ON public.action_events USING btree (plant_id);


--
-- Name: ix_actions_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_actions_due ON public.actions USING btree (plant_id, due_date);


--
-- Name: ix_actions_owner_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_actions_owner_id ON public.actions USING btree (owner_id);


--
-- Name: ix_actions_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_actions_plant_id ON public.actions USING btree (plant_id);


--
-- Name: ix_actions_reason_code_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_actions_reason_code_id ON public.actions USING btree (reason_code_id);


--
-- Name: ix_actions_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_actions_state ON public.actions USING btree (plant_id, state);


--
-- Name: ix_actions_station_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_actions_station_id ON public.actions USING btree (station_id);


--
-- Name: ix_alert_recipients_plant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_recipients_plant ON public.alert_recipients USING btree (plant_id);


--
-- Name: ix_alert_recipients_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_recipients_plant_id ON public.alert_recipients USING btree (plant_id);


--
-- Name: ix_audit_log_entity_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_entity_id ON public.audit_log USING btree (entity_id);


--
-- Name: ix_audit_log_entity_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_entity_type ON public.audit_log USING btree (entity_type);


--
-- Name: ix_audit_log_occurred_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_occurred_at ON public.audit_log USING btree (occurred_at);


--
-- Name: ix_audit_log_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_log_plant_id ON public.audit_log USING btree (plant_id);


--
-- Name: ix_audit_packs_plant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_packs_plant ON public.audit_packs USING btree (plant_id);


--
-- Name: ix_audit_packs_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_packs_plant_id ON public.audit_packs USING btree (plant_id);


--
-- Name: ix_coach_briefings_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_coach_briefings_plant_id ON public.coach_briefings USING btree (plant_id);


--
-- Name: ix_coach_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_coach_date ON public.coach_briefings USING btree (plant_id, business_date);


--
-- Name: ix_compliance_clauses_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_compliance_clauses_plant_id ON public.compliance_clauses USING btree (plant_id);


--
-- Name: ix_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_compliance_status ON public.compliance_clauses USING btree (plant_id, standard, status);


--
-- Name: ix_copilot_queries_plant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_copilot_queries_plant ON public.copilot_queries USING btree (plant_id);


--
-- Name: ix_copilot_queries_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_copilot_queries_plant_id ON public.copilot_queries USING btree (plant_id);


--
-- Name: ix_cost_families_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cost_families_plant_id ON public.cost_families USING btree (plant_id);


--
-- Name: ix_cost_rates_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cost_rates_lookup ON public.cost_rates USING btree (plant_id, scope, scope_ref, effective_from);


--
-- Name: ix_cost_rates_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cost_rates_plant_id ON public.cost_rates USING btree (plant_id);


--
-- Name: ix_defect_codes_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_defect_codes_plant_id ON public.defect_codes USING btree (plant_id);


--
-- Name: ix_dispatch_records_lot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispatch_records_lot_id ON public.dispatch_records USING btree (lot_id);


--
-- Name: ix_dispatch_records_plant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispatch_records_plant ON public.dispatch_records USING btree (plant_id);


--
-- Name: ix_dispatch_records_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_dispatch_records_plant_id ON public.dispatch_records USING btree (plant_id);


--
-- Name: ix_downtime_business_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_downtime_business_date ON public.downtime_events USING btree (plant_id, business_date);


--
-- Name: ix_downtime_events_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_downtime_events_plant_id ON public.downtime_events USING btree (plant_id);


--
-- Name: ix_downtime_events_reason_code_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_downtime_events_reason_code_id ON public.downtime_events USING btree (reason_code_id);


--
-- Name: ix_downtime_events_shift_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_downtime_events_shift_session_id ON public.downtime_events USING btree (shift_session_id);


--
-- Name: ix_downtime_events_station_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_downtime_events_station_id ON public.downtime_events USING btree (station_id);


--
-- Name: ix_downtime_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_downtime_open ON public.downtime_events USING btree (plant_id, status, started_at);


--
-- Name: ix_downtime_station_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_downtime_station_time ON public.downtime_events USING btree (plant_id, station_id, started_at);


--
-- Name: ix_escalations_due; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_escalations_due ON public.escalations USING btree (plant_id, status, due_at);


--
-- Name: ix_escalations_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_escalations_event ON public.escalations USING btree (downtime_event_id);


--
-- Name: ix_escalations_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_escalations_plant_id ON public.escalations USING btree (plant_id);


--
-- Name: ix_job_orders_part_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_orders_part_id ON public.job_orders USING btree (part_id);


--
-- Name: ix_job_orders_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_orders_plant_id ON public.job_orders USING btree (plant_id);


--
-- Name: ix_job_orders_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_orders_status ON public.job_orders USING btree (plant_id, status);


--
-- Name: ix_job_sessions_job_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_sessions_job_order_id ON public.job_sessions USING btree (job_order_id);


--
-- Name: ix_job_sessions_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_sessions_plant_id ON public.job_sessions USING btree (plant_id);


--
-- Name: ix_job_sessions_shift; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_sessions_shift ON public.job_sessions USING btree (shift_session_id);


--
-- Name: ix_job_sessions_station_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_sessions_station_id ON public.job_sessions USING btree (station_id);


--
-- Name: ix_job_sessions_station_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_job_sessions_station_open ON public.job_sessions USING btree (plant_id, station_id, ended_at);


--
-- Name: ix_kaizen_ideas_action_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_kaizen_ideas_action_id ON public.kaizen_ideas USING btree (action_id);


--
-- Name: ix_kaizen_ideas_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_kaizen_ideas_plant_id ON public.kaizen_ideas USING btree (plant_id);


--
-- Name: ix_kaizen_ideas_station_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_kaizen_ideas_station_id ON public.kaizen_ideas USING btree (station_id);


--
-- Name: ix_kaizen_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_kaizen_status ON public.kaizen_ideas USING btree (plant_id, status);


--
-- Name: ix_lots_job_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lots_job_order_id ON public.lots USING btree (job_order_id);


--
-- Name: ix_lots_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_lots_plant_id ON public.lots USING btree (plant_id);


--
-- Name: ix_margin_band_values_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_margin_band_values_plant_id ON public.margin_band_values USING btree (plant_id);


--
-- Name: ix_memory_entries_action_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_action_id ON public.memory_entries USING btree (action_id);


--
-- Name: ix_memory_entries_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_plant_id ON public.memory_entries USING btree (plant_id);


--
-- Name: ix_memory_entries_station_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_entries_station_id ON public.memory_entries USING btree (station_id);


--
-- Name: ix_memory_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_fts ON public.memory_entries USING gin (to_tsvector('english'::regconfig, ((((COALESCE(problem_summary, ''::text) || ' '::text) || COALESCE(what_fixed_it, ''::text)) || ' '::text) || COALESCE(check_first_next_time, ''::text))));


--
-- Name: ix_memory_search; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memory_search ON public.memory_entries USING btree (plant_id, created_at);


--
-- Name: ix_operator_skills_operator_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operator_skills_operator_id ON public.operator_skills USING btree (operator_id);


--
-- Name: ix_operator_skills_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operator_skills_plant_id ON public.operator_skills USING btree (plant_id);


--
-- Name: ix_operator_skills_station_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operator_skills_station_id ON public.operator_skills USING btree (station_id);


--
-- Name: ix_operators_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operators_plant_id ON public.operators USING btree (plant_id);


--
-- Name: ix_parts_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_parts_plant_id ON public.parts USING btree (plant_id);


--
-- Name: ix_production_business_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_business_date ON public.production_counts USING btree (plant_id, business_date);


--
-- Name: ix_production_counts_job_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_counts_job_session_id ON public.production_counts USING btree (job_session_id);


--
-- Name: ix_production_counts_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_counts_plant_id ON public.production_counts USING btree (plant_id);


--
-- Name: ix_production_counts_shift_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_counts_shift_session_id ON public.production_counts USING btree (shift_session_id);


--
-- Name: ix_production_counts_station_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_counts_station_id ON public.production_counts USING btree (station_id);


--
-- Name: ix_production_station_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_station_time ON public.production_counts USING btree (plant_id, station_id, recorded_at);


--
-- Name: ix_production_targets_plant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_targets_plant ON public.production_targets USING btree (plant_id);


--
-- Name: ix_production_targets_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_production_targets_plant_id ON public.production_targets USING btree (plant_id);


--
-- Name: ix_quality_business_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_business_date ON public.quality_records USING btree (plant_id, business_date);


--
-- Name: ix_quality_part; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_part ON public.quality_records USING btree (plant_id, part_id, recorded_at);


--
-- Name: ix_quality_records_lot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_records_lot_id ON public.quality_records USING btree (lot_id);


--
-- Name: ix_quality_records_part_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_records_part_id ON public.quality_records USING btree (part_id);


--
-- Name: ix_quality_records_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_records_plant_id ON public.quality_records USING btree (plant_id);


--
-- Name: ix_quality_records_shift_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_records_shift_session_id ON public.quality_records USING btree (shift_session_id);


--
-- Name: ix_quality_records_station_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_quality_records_station_id ON public.quality_records USING btree (station_id);


--
-- Name: ix_readiness_assessments_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_readiness_assessments_plant_id ON public.readiness_assessments USING btree (plant_id);


--
-- Name: ix_reason_codes_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_reason_codes_plant_id ON public.reason_codes USING btree (plant_id);


--
-- Name: ix_recovered_profit_entries_action_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recovered_profit_entries_action_id ON public.recovered_profit_entries USING btree (action_id);


--
-- Name: ix_recovered_profit_entries_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recovered_profit_entries_plant_id ON public.recovered_profit_entries USING btree (plant_id);


--
-- Name: ix_root_cause_analyses_plant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_root_cause_analyses_plant ON public.root_cause_analyses USING btree (plant_id);


--
-- Name: ix_root_cause_analyses_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_root_cause_analyses_plant_id ON public.root_cause_analyses USING btree (plant_id);


--
-- Name: ix_routing_operations_part_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_routing_operations_part_id ON public.routing_operations USING btree (part_id);


--
-- Name: ix_routing_operations_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_routing_operations_plant_id ON public.routing_operations USING btree (plant_id);


--
-- Name: ix_routing_part_seq; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_routing_part_seq ON public.routing_operations USING btree (part_id, sequence);


--
-- Name: ix_shift_sessions_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_shift_sessions_date ON public.shift_sessions USING btree (plant_id, business_date);


--
-- Name: ix_shift_sessions_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_shift_sessions_plant_id ON public.shift_sessions USING btree (plant_id);


--
-- Name: ix_shift_sessions_shift_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_shift_sessions_shift_id ON public.shift_sessions USING btree (shift_id);


--
-- Name: ix_shifts_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_shifts_plant_id ON public.shifts USING btree (plant_id);


--
-- Name: ix_stage_completions_lot_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stage_completions_lot_id ON public.stage_completions USING btree (lot_id);


--
-- Name: ix_stage_completions_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stage_completions_plant_id ON public.stage_completions USING btree (plant_id);


--
-- Name: ix_stage_lot; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stage_lot ON public.stage_completions USING btree (plant_id, lot_id, completed_at);


--
-- Name: ix_stations_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_stations_plant_id ON public.stations USING btree (plant_id);


--
-- Name: ix_template_uploads_plant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_template_uploads_plant ON public.template_uploads USING btree (plant_id);


--
-- Name: ix_template_uploads_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_template_uploads_plant_id ON public.template_uploads USING btree (plant_id);


--
-- Name: ix_training_needs_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_training_needs_plant_id ON public.training_needs USING btree (plant_id);


--
-- Name: ix_training_needs_station_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_training_needs_station_id ON public.training_needs USING btree (station_id);


--
-- Name: ix_training_value; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_training_value ON public.training_needs USING btree (plant_id, annual_loss_paise);


--
-- Name: ix_user_plant_roles_plant_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_plant_roles_plant_id ON public.user_plant_roles USING btree (plant_id);


--
-- Name: ix_user_plant_roles_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_user_plant_roles_user_id ON public.user_plant_roles USING btree (user_id);


--
-- Name: uq_one_open_downtime_per_station; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_one_open_downtime_per_station ON public.downtime_events USING btree (station_id) WHERE ((status)::text = 'open'::text);


--
-- Name: action_events action_events_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_events
    ADD CONSTRAINT action_events_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.actions(id) ON DELETE CASCADE;


--
-- Name: action_events action_events_downtime_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_events
    ADD CONSTRAINT action_events_downtime_event_id_fkey FOREIGN KEY (downtime_event_id) REFERENCES public.downtime_events(id) ON DELETE CASCADE;


--
-- Name: action_events action_events_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.action_events
    ADD CONSTRAINT action_events_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: actions actions_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actions
    ADD CONSTRAINT actions_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: actions actions_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actions
    ADD CONSTRAINT actions_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: actions actions_reason_code_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actions
    ADD CONSTRAINT actions_reason_code_id_fkey FOREIGN KEY (reason_code_id) REFERENCES public.reason_codes(id) ON DELETE SET NULL;


--
-- Name: actions actions_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actions
    ADD CONSTRAINT actions_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE SET NULL;


--
-- Name: alert_recipients alert_recipients_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_recipients
    ADD CONSTRAINT alert_recipients_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: alert_recipients alert_recipients_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_recipients
    ADD CONSTRAINT alert_recipients_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: audit_log audit_log_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: audit_log audit_log_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: audit_packs audit_packs_generated_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_packs
    ADD CONSTRAINT audit_packs_generated_by_id_fkey FOREIGN KEY (generated_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: audit_packs audit_packs_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_packs
    ADD CONSTRAINT audit_packs_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: coach_briefings coach_briefings_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coach_briefings
    ADD CONSTRAINT coach_briefings_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: coach_briefings coach_briefings_recipient_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coach_briefings
    ADD CONSTRAINT coach_briefings_recipient_id_fkey FOREIGN KEY (recipient_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: coach_briefings coach_briefings_shift_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.coach_briefings
    ADD CONSTRAINT coach_briefings_shift_id_fkey FOREIGN KEY (shift_id) REFERENCES public.shifts(id) ON DELETE SET NULL;


--
-- Name: compliance_clauses compliance_clauses_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compliance_clauses
    ADD CONSTRAINT compliance_clauses_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: copilot_queries copilot_queries_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.copilot_queries
    ADD CONSTRAINT copilot_queries_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: copilot_queries copilot_queries_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.copilot_queries
    ADD CONSTRAINT copilot_queries_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: cost_families cost_families_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_families
    ADD CONSTRAINT cost_families_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: cost_rates cost_rates_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cost_rates
    ADD CONSTRAINT cost_rates_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: defect_codes defect_codes_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.defect_codes
    ADD CONSTRAINT defect_codes_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: dispatch_records dispatch_records_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_records
    ADD CONSTRAINT dispatch_records_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES public.lots(id) ON DELETE CASCADE;


--
-- Name: dispatch_records dispatch_records_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dispatch_records
    ADD CONSTRAINT dispatch_records_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: downtime_events downtime_events_job_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.downtime_events
    ADD CONSTRAINT downtime_events_job_session_id_fkey FOREIGN KEY (job_session_id) REFERENCES public.job_sessions(id) ON DELETE SET NULL;


--
-- Name: downtime_events downtime_events_logged_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.downtime_events
    ADD CONSTRAINT downtime_events_logged_by_id_fkey FOREIGN KEY (logged_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: downtime_events downtime_events_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.downtime_events
    ADD CONSTRAINT downtime_events_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: downtime_events downtime_events_reason_code_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.downtime_events
    ADD CONSTRAINT downtime_events_reason_code_id_fkey FOREIGN KEY (reason_code_id) REFERENCES public.reason_codes(id) ON DELETE RESTRICT;


--
-- Name: downtime_events downtime_events_shift_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.downtime_events
    ADD CONSTRAINT downtime_events_shift_session_id_fkey FOREIGN KEY (shift_session_id) REFERENCES public.shift_sessions(id) ON DELETE SET NULL;


--
-- Name: downtime_events downtime_events_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.downtime_events
    ADD CONSTRAINT downtime_events_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE RESTRICT;


--
-- Name: escalations escalations_downtime_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.escalations
    ADD CONSTRAINT escalations_downtime_event_id_fkey FOREIGN KEY (downtime_event_id) REFERENCES public.downtime_events(id) ON DELETE CASCADE;


--
-- Name: escalations escalations_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.escalations
    ADD CONSTRAINT escalations_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: job_orders job_orders_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_orders
    ADD CONSTRAINT job_orders_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id) ON DELETE RESTRICT;


--
-- Name: job_orders job_orders_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_orders
    ADD CONSTRAINT job_orders_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: job_sessions job_sessions_job_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_sessions
    ADD CONSTRAINT job_sessions_job_order_id_fkey FOREIGN KEY (job_order_id) REFERENCES public.job_orders(id) ON DELETE CASCADE;


--
-- Name: job_sessions job_sessions_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_sessions
    ADD CONSTRAINT job_sessions_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES public.lots(id) ON DELETE SET NULL;


--
-- Name: job_sessions job_sessions_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_sessions
    ADD CONSTRAINT job_sessions_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.operators(id) ON DELETE SET NULL;


--
-- Name: job_sessions job_sessions_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_sessions
    ADD CONSTRAINT job_sessions_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: job_sessions job_sessions_previous_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_sessions
    ADD CONSTRAINT job_sessions_previous_session_id_fkey FOREIGN KEY (previous_session_id) REFERENCES public.job_sessions(id) ON DELETE SET NULL;


--
-- Name: job_sessions job_sessions_routing_operation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_sessions
    ADD CONSTRAINT job_sessions_routing_operation_id_fkey FOREIGN KEY (routing_operation_id) REFERENCES public.routing_operations(id) ON DELETE SET NULL;


--
-- Name: job_sessions job_sessions_shift_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_sessions
    ADD CONSTRAINT job_sessions_shift_session_id_fkey FOREIGN KEY (shift_session_id) REFERENCES public.shift_sessions(id) ON DELETE CASCADE;


--
-- Name: job_sessions job_sessions_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_sessions
    ADD CONSTRAINT job_sessions_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE RESTRICT;


--
-- Name: kaizen_ideas kaizen_ideas_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kaizen_ideas
    ADD CONSTRAINT kaizen_ideas_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.actions(id) ON DELETE SET NULL;


--
-- Name: kaizen_ideas kaizen_ideas_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kaizen_ideas
    ADD CONSTRAINT kaizen_ideas_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: kaizen_ideas kaizen_ideas_reason_code_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kaizen_ideas
    ADD CONSTRAINT kaizen_ideas_reason_code_id_fkey FOREIGN KEY (reason_code_id) REFERENCES public.reason_codes(id) ON DELETE SET NULL;


--
-- Name: kaizen_ideas kaizen_ideas_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kaizen_ideas
    ADD CONSTRAINT kaizen_ideas_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE SET NULL;


--
-- Name: kaizen_ideas kaizen_ideas_submitted_by_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kaizen_ideas
    ADD CONSTRAINT kaizen_ideas_submitted_by_operator_id_fkey FOREIGN KEY (submitted_by_operator_id) REFERENCES public.operators(id) ON DELETE SET NULL;


--
-- Name: lots lots_job_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lots
    ADD CONSTRAINT lots_job_order_id_fkey FOREIGN KEY (job_order_id) REFERENCES public.job_orders(id) ON DELETE SET NULL;


--
-- Name: lots lots_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lots
    ADD CONSTRAINT lots_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: margin_band_values margin_band_values_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.margin_band_values
    ADD CONSTRAINT margin_band_values_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: memory_entries memory_entries_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_entries
    ADD CONSTRAINT memory_entries_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.actions(id) ON DELETE SET NULL;


--
-- Name: memory_entries memory_entries_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_entries
    ADD CONSTRAINT memory_entries_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id) ON DELETE SET NULL;


--
-- Name: memory_entries memory_entries_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_entries
    ADD CONSTRAINT memory_entries_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: memory_entries memory_entries_reason_code_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_entries
    ADD CONSTRAINT memory_entries_reason_code_id_fkey FOREIGN KEY (reason_code_id) REFERENCES public.reason_codes(id) ON DELETE SET NULL;


--
-- Name: memory_entries memory_entries_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_entries
    ADD CONSTRAINT memory_entries_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE SET NULL;


--
-- Name: operator_skills operator_skills_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_skills
    ADD CONSTRAINT operator_skills_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.operators(id) ON DELETE CASCADE;


--
-- Name: operator_skills operator_skills_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_skills
    ADD CONSTRAINT operator_skills_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: operator_skills operator_skills_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_skills
    ADD CONSTRAINT operator_skills_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE CASCADE;


--
-- Name: operators operators_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operators
    ADD CONSTRAINT operators_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: parts parts_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parts
    ADD CONSTRAINT parts_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: production_counts production_counts_job_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_counts
    ADD CONSTRAINT production_counts_job_session_id_fkey FOREIGN KEY (job_session_id) REFERENCES public.job_sessions(id) ON DELETE SET NULL;


--
-- Name: production_counts production_counts_logged_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_counts
    ADD CONSTRAINT production_counts_logged_by_id_fkey FOREIGN KEY (logged_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: production_counts production_counts_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_counts
    ADD CONSTRAINT production_counts_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: production_counts production_counts_shift_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_counts
    ADD CONSTRAINT production_counts_shift_session_id_fkey FOREIGN KEY (shift_session_id) REFERENCES public.shift_sessions(id) ON DELETE SET NULL;


--
-- Name: production_counts production_counts_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_counts
    ADD CONSTRAINT production_counts_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE RESTRICT;


--
-- Name: production_targets production_targets_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_targets
    ADD CONSTRAINT production_targets_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: production_targets production_targets_shift_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_targets
    ADD CONSTRAINT production_targets_shift_id_fkey FOREIGN KEY (shift_id) REFERENCES public.shifts(id) ON DELETE CASCADE;


--
-- Name: production_targets production_targets_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.production_targets
    ADD CONSTRAINT production_targets_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE CASCADE;


--
-- Name: quality_records quality_records_inspector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_records
    ADD CONSTRAINT quality_records_inspector_id_fkey FOREIGN KEY (inspector_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: quality_records quality_records_job_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_records
    ADD CONSTRAINT quality_records_job_session_id_fkey FOREIGN KEY (job_session_id) REFERENCES public.job_sessions(id) ON DELETE SET NULL;


--
-- Name: quality_records quality_records_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_records
    ADD CONSTRAINT quality_records_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES public.lots(id) ON DELETE SET NULL;


--
-- Name: quality_records quality_records_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_records
    ADD CONSTRAINT quality_records_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id) ON DELETE RESTRICT;


--
-- Name: quality_records quality_records_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_records
    ADD CONSTRAINT quality_records_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: quality_records quality_records_shift_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_records
    ADD CONSTRAINT quality_records_shift_session_id_fkey FOREIGN KEY (shift_session_id) REFERENCES public.shift_sessions(id) ON DELETE SET NULL;


--
-- Name: quality_records quality_records_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quality_records
    ADD CONSTRAINT quality_records_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE SET NULL;


--
-- Name: readiness_assessments readiness_assessments_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.readiness_assessments
    ADD CONSTRAINT readiness_assessments_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE SET NULL;


--
-- Name: reason_codes reason_codes_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reason_codes
    ADD CONSTRAINT reason_codes_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: recovered_profit_entries recovered_profit_entries_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recovered_profit_entries
    ADD CONSTRAINT recovered_profit_entries_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.actions(id) ON DELETE CASCADE;


--
-- Name: recovered_profit_entries recovered_profit_entries_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.recovered_profit_entries
    ADD CONSTRAINT recovered_profit_entries_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: root_cause_analyses root_cause_analyses_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.root_cause_analyses
    ADD CONSTRAINT root_cause_analyses_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.actions(id) ON DELETE CASCADE;


--
-- Name: root_cause_analyses root_cause_analyses_assigned_to_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.root_cause_analyses
    ADD CONSTRAINT root_cause_analyses_assigned_to_id_fkey FOREIGN KEY (assigned_to_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: root_cause_analyses root_cause_analyses_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.root_cause_analyses
    ADD CONSTRAINT root_cause_analyses_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: routing_operations routing_operations_cost_family_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routing_operations
    ADD CONSTRAINT routing_operations_cost_family_id_fkey FOREIGN KEY (cost_family_id) REFERENCES public.cost_families(id) ON DELETE SET NULL;


--
-- Name: routing_operations routing_operations_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routing_operations
    ADD CONSTRAINT routing_operations_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id) ON DELETE CASCADE;


--
-- Name: routing_operations routing_operations_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.routing_operations
    ADD CONSTRAINT routing_operations_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: shift_sessions shift_sessions_closed_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_sessions
    ADD CONSTRAINT shift_sessions_closed_by_id_fkey FOREIGN KEY (closed_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: shift_sessions shift_sessions_override_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_sessions
    ADD CONSTRAINT shift_sessions_override_by_id_fkey FOREIGN KEY (override_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: shift_sessions shift_sessions_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_sessions
    ADD CONSTRAINT shift_sessions_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: shift_sessions shift_sessions_shift_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_sessions
    ADD CONSTRAINT shift_sessions_shift_id_fkey FOREIGN KEY (shift_id) REFERENCES public.shifts(id) ON DELETE RESTRICT;


--
-- Name: shift_sessions shift_sessions_supervisor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shift_sessions
    ADD CONSTRAINT shift_sessions_supervisor_id_fkey FOREIGN KEY (supervisor_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: shifts shifts_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT shifts_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: stage_completions stage_completions_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stage_completions
    ADD CONSTRAINT stage_completions_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES public.lots(id) ON DELETE CASCADE;


--
-- Name: stage_completions stage_completions_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stage_completions
    ADD CONSTRAINT stage_completions_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: stage_completions stage_completions_routing_operation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stage_completions
    ADD CONSTRAINT stage_completions_routing_operation_id_fkey FOREIGN KEY (routing_operation_id) REFERENCES public.routing_operations(id) ON DELETE RESTRICT;


--
-- Name: stage_completions stage_completions_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stage_completions
    ADD CONSTRAINT stage_completions_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE SET NULL;


--
-- Name: stations stations_cost_family_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT stations_cost_family_id_fkey FOREIGN KEY (cost_family_id) REFERENCES public.cost_families(id) ON DELETE SET NULL;


--
-- Name: stations stations_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stations
    ADD CONSTRAINT stations_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: template_uploads template_uploads_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.template_uploads
    ADD CONSTRAINT template_uploads_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: template_uploads template_uploads_uploaded_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.template_uploads
    ADD CONSTRAINT template_uploads_uploaded_by_id_fkey FOREIGN KEY (uploaded_by_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: training_needs training_needs_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_needs
    ADD CONSTRAINT training_needs_action_id_fkey FOREIGN KEY (action_id) REFERENCES public.actions(id) ON DELETE SET NULL;


--
-- Name: training_needs training_needs_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_needs
    ADD CONSTRAINT training_needs_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.operators(id) ON DELETE CASCADE;


--
-- Name: training_needs training_needs_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_needs
    ADD CONSTRAINT training_needs_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: training_needs training_needs_station_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.training_needs
    ADD CONSTRAINT training_needs_station_id_fkey FOREIGN KEY (station_id) REFERENCES public.stations(id) ON DELETE CASCADE;


--
-- Name: user_plant_roles user_plant_roles_plant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_plant_roles
    ADD CONSTRAINT user_plant_roles_plant_id_fkey FOREIGN KEY (plant_id) REFERENCES public.plants(id) ON DELETE CASCADE;


--
-- Name: user_plant_roles user_plant_roles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_plant_roles
    ADD CONSTRAINT user_plant_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: action_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.action_events ENABLE ROW LEVEL SECURITY;

--
-- Name: actions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.actions ENABLE ROW LEVEL SECURITY;

--
-- Name: alert_recipients; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.alert_recipients ENABLE ROW LEVEL SECURITY;

--
-- Name: audit_log; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;

--
-- Name: audit_packs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.audit_packs ENABLE ROW LEVEL SECURITY;

--
-- Name: coach_briefings; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.coach_briefings ENABLE ROW LEVEL SECURITY;

--
-- Name: compliance_clauses; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.compliance_clauses ENABLE ROW LEVEL SECURITY;

--
-- Name: copilot_queries; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.copilot_queries ENABLE ROW LEVEL SECURITY;

--
-- Name: cost_families; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cost_families ENABLE ROW LEVEL SECURITY;

--
-- Name: cost_rates; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.cost_rates ENABLE ROW LEVEL SECURITY;

--
-- Name: defect_codes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.defect_codes ENABLE ROW LEVEL SECURITY;

--
-- Name: dispatch_records; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.dispatch_records ENABLE ROW LEVEL SECURITY;

--
-- Name: downtime_events; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.downtime_events ENABLE ROW LEVEL SECURITY;

--
-- Name: escalations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.escalations ENABLE ROW LEVEL SECURITY;

--
-- Name: job_orders; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.job_orders ENABLE ROW LEVEL SECURITY;

--
-- Name: job_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.job_sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: kaizen_ideas; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.kaizen_ideas ENABLE ROW LEVEL SECURITY;

--
-- Name: lots; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lots ENABLE ROW LEVEL SECURITY;

--
-- Name: margin_band_values; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.margin_band_values ENABLE ROW LEVEL SECURITY;

--
-- Name: memory_entries; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.memory_entries ENABLE ROW LEVEL SECURITY;

--
-- Name: operator_skills; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.operator_skills ENABLE ROW LEVEL SECURITY;

--
-- Name: operators; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.operators ENABLE ROW LEVEL SECURITY;

--
-- Name: parts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.parts ENABLE ROW LEVEL SECURITY;

--
-- Name: action_events plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.action_events USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: actions plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.actions USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: alert_recipients plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.alert_recipients USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: audit_log plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.audit_log USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: audit_packs plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.audit_packs USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: coach_briefings plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.coach_briefings USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: compliance_clauses plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.compliance_clauses USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: copilot_queries plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.copilot_queries USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: cost_families plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.cost_families USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: cost_rates plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.cost_rates USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: defect_codes plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.defect_codes USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: dispatch_records plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.dispatch_records USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: downtime_events plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.downtime_events USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: escalations plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.escalations USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: job_orders plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.job_orders USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: job_sessions plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.job_sessions USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: kaizen_ideas plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.kaizen_ideas USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: lots plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.lots USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: margin_band_values plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.margin_band_values USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: memory_entries plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.memory_entries USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: operator_skills plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.operator_skills USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: operators plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.operators USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: parts plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.parts USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: production_counts plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.production_counts USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: production_targets plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.production_targets USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: quality_records plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.quality_records USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: reason_codes plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.reason_codes USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: recovered_profit_entries plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.recovered_profit_entries USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: root_cause_analyses plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.root_cause_analyses USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: routing_operations plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.routing_operations USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: shift_sessions plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.shift_sessions USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: shifts plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.shifts USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: stage_completions plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.stage_completions USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: stations plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.stations USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: template_uploads plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.template_uploads USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: training_needs plant_isolation; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY plant_isolation ON public.training_needs USING (((current_setting('app.current_plant'::text, true) IS NULL) OR (current_setting('app.current_plant'::text, true) = ''::text) OR ((plant_id)::text = current_setting('app.current_plant'::text, true))));


--
-- Name: production_counts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.production_counts ENABLE ROW LEVEL SECURITY;

--
-- Name: production_targets; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.production_targets ENABLE ROW LEVEL SECURITY;

--
-- Name: quality_records; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.quality_records ENABLE ROW LEVEL SECURITY;

--
-- Name: reason_codes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.reason_codes ENABLE ROW LEVEL SECURITY;

--
-- Name: recovered_profit_entries; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.recovered_profit_entries ENABLE ROW LEVEL SECURITY;

--
-- Name: root_cause_analyses; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.root_cause_analyses ENABLE ROW LEVEL SECURITY;

--
-- Name: routing_operations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.routing_operations ENABLE ROW LEVEL SECURITY;

--
-- Name: shift_sessions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.shift_sessions ENABLE ROW LEVEL SECURITY;

--
-- Name: shifts; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.shifts ENABLE ROW LEVEL SECURITY;

--
-- Name: stage_completions; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.stage_completions ENABLE ROW LEVEL SECURITY;

--
-- Name: stations; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.stations ENABLE ROW LEVEL SECURITY;

--
-- Name: template_uploads; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.template_uploads ENABLE ROW LEVEL SECURITY;

--
-- Name: training_needs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.training_needs ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--


-- Stamp the revision so a later 'alembic upgrade head' is a no-op.
INSERT INTO public.alembic_version (version_num) VALUES ('0ba4b6bb4343');
