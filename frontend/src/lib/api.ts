"use client";

import type {
  Action, ClosureCheck, CoachBriefing, CopilotAnswer, DefectCode, DowntimeEvent,
  JobOrder, LossReport, MeetingPack, MemoryEntry, Operator, OwnerHome, Part,
  QualityPayload, QualityResult, Rca, ReadinessQuestion, ReadinessResult, ReasonCode, Session,
  Shift, ShiftKpis, ShiftSession, Station, StationTile,
  AdminUser, CostRate, CostRateInput, Escalation, JobOrderCreate, JobOrderUpdate,
  MasterResource, MasterShapes, PlantCreate, PlantSettings, PlantSettingsUpdate,
  UploadConfirmResult, UploadPreview, UserCreate, UserUpdate,
  AuditPack, ComplianceOverview, KaizenContributor, KaizenIdea, SkillMatrix, TrainingNeed,
  LotFlow, LotSearchResult, LotTrace, WipLot,
  ExecutiveKpis, MaintenanceKpis, MonthlyReport, QualityKpis, SupervisorKpis,
  Attachment, AuditEntityType, AuditEntry, DowntimeCorrection, HoldStatus, NotificationChannels,
  NotificationLog, OnboardingDefaults, PushPublicKey, QualityCorrection, ShiftEntries,
  ShiftEntryProduction, SyncHold, SyncOpIn, SyncResult,
} from "@/types/api";
import { reportRequest } from "@/lib/offline/status";

const BASE = "/api/v1";
const TOKEN_KEY = "fm.session";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message);
  }
}

/** Status 0: the request never got an answer — no network, or the API is unreachable. */
export const NO_CONNECTION = "No connection — this needs the network. Check the tablet's Wi-Fi and try again.";

export function loadSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(TOKEN_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    // Private windows and cleared site data both throw here. A supervisor who
    // has to log in again is a nuisance; a white screen is a stopped line.
    return null;
  }
}

export function saveSession(session: Session): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, JSON.stringify(session));
  } catch {
    /* non-fatal: the session lives in memory for this tab */
  }
}

export function clearSession(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

async function request<T>(
  path: string,
  options: RequestInit & { auth?: boolean } = {},
): Promise<T> {
  const { auth = true, headers, ...rest } = options;
  const session = auth ? loadSession() : null;
  // A multipart body must set its own Content-Type, boundary included.
  const multipart = typeof FormData !== "undefined" && rest.body instanceof FormData;

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...rest,
      headers: {
        ...(multipart ? {} : { "Content-Type": "application/json" }),
        ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}),
        ...headers,
      },
    });
  } catch (e) {
    // fetch only throws when nothing answered. "Failed to fetch" means nothing
    // to a supervisor; say what it is. The header pill turns red from here too.
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    reportRequest(false);
    throw new ApiError(0, NO_CONNECTION);
  }
  // A gateway error is the proxy saying the API is not there — as good as offline.
  reportRequest(response.status < 502);

  if (response.status === 401 && auth) {
    clearSession();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Session expired");
  }

  if (!response.ok) {
    // Read the body once: a proxy error page is text, and a second read of a
    // consumed body throws instead of reporting the real failure.
    const raw = await response.text();
    let detail: unknown;
    try {
      detail = (JSON.parse(raw) as { detail?: unknown })?.detail;
    } catch {
      // A short plain-text reason is worth showing; an HTML error page is not.
      detail = raw && raw.length < 300 && !raw.trimStart().startsWith("<") ? raw : undefined;
    }
    // FastAPI validation errors arrive as a list of {loc, msg}; a form needs
    // the sentence, not "Request failed (422)".
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((item: { loc?: unknown[]; msg?: string }) => {
                const field = item.loc?.filter((part) => part !== "body").join(".");
                return field ? `${field}: ${item.msg}` : item.msg;
              })
              .join("; ")
          : (detail as { message?: string })?.message ?? `Request failed (${response.status})`;
    throw new ApiError(response.status, message, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const get = <T,>(path: string) => request<T>(path);
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
const patch = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const put = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });
/** Multipart POST. The bearer token still goes on; the JSON header does not. */
const upload = <T,>(path: string, form: FormData) =>
  request<T>(path, { method: "POST", body: form });

/** An attachment url is an API path ("/api/v1/attachments/…"); an <img> cannot send the token. */
async function blob(url: string): Promise<Blob> {
  const session = loadSession();
  let response: Response;
  try {
    response = await fetch(url.startsWith(BASE) ? url : `${BASE}${url}`, {
      headers: session ? { Authorization: `Bearer ${session.access_token}` } : {},
    });
  } catch {
    reportRequest(false);
    throw new ApiError(0, NO_CONNECTION);
  }
  reportRequest(response.status < 502);
  if (!response.ok) throw new ApiError(response.status, `Could not load the photo (${response.status})`);
  return response.blob();
}

const inactive = (include?: boolean) => (include ? "include_inactive=true" : "");
const query = (...parts: string[]) => {
  const joined = parts.filter(Boolean).join("&");
  return joined ? `?${joined}` : "";
};

export const api = {
  // --- auth ---------------------------------------------------------------
  login: (identifier: string, password: string, plantCode?: string) =>
    request<Session>("/auth/login", {
      method: "POST",
      auth: false,
      body: JSON.stringify({ identifier, password, plant_code: plantCode }),
    }),
  me: () => get<Session>("/auth/me"),
  switchPlant: (refreshToken: string, plantId: string) =>
    request<Session>("/auth/refresh", {
      method: "POST",
      auth: false,
      body: JSON.stringify({ refresh_token: refreshToken, plant_id: plantId }),
    }),

  // --- masters ------------------------------------------------------------
  stations: () => get<Station[]>("/masters/stations"),
  reasonCodes: () => get<ReasonCode[]>("/masters/reason-codes"),
  defectCodes: () => get<DefectCode[]>("/masters/defect-codes"),
  parts: () => get<Part[]>("/masters/parts"),
  operators: () => get<Operator[]>("/masters/operators"),
  shifts: () => get<Shift[]>("/masters/shifts"),
  // Runnable jobs only (planned, running, paused) unless closed ones are asked for.
  jobOrders: () => get<JobOrder[]>("/masters/job-orders"),
  jobOrderList: (includeClosed: boolean) =>
    get<JobOrder[]>(`/masters/job-orders${includeClosed ? "?include_closed=true" : ""}`),
  createJobOrder: (payload: JobOrderCreate) => post<JobOrder>("/masters/job-orders", payload),
  updateJobOrder: (id: string, payload: JobOrderUpdate) =>
    patch<JobOrder>(`/masters/job-orders/${id}`, payload),
  repeatJobOrder: (id: string, payload: { planned_quantity?: number; due_date?: string } = {}) =>
    post<JobOrder>(`/masters/job-orders/${id}/repeat`, payload),

  // --- settings: master data CRUD (owner, consultant, admin) --------------
  // No hard deletes anywhere: a row is retired with `is_active: false`, so
  // history that points at it keeps its meaning. Parts are fetched with
  // `detail=true`, which adds the owner-confidential margins.
  masterList: <K extends MasterResource>(resource: K, includeInactive = false) =>
    get<Array<MasterShapes[K]["row"]>>(
      `/masters/${resource}${query(inactive(includeInactive), resource === "parts" ? "detail=true" : "")}`,
    ),
  createMaster: <K extends MasterResource>(resource: K, payload: MasterShapes[K]["input"]) =>
    post<MasterShapes[K]["row"]>(`/masters/${resource}`, payload),
  updateMaster: <K extends MasterResource>(
    resource: K,
    id: string,
    payload: Partial<MasterShapes[K]["input"]>,
  ) => patch<MasterShapes[K]["row"]>(`/masters/${resource}/${id}`, payload),
  costRates: () => get<CostRate[]>("/masters/cost-rates"),
  createCostRate: (payload: CostRateInput) => post<CostRate>("/masters/cost-rates", payload),

  // --- administration ------------------------------------------------------
  users: () => get<AdminUser[]>("/admin/users"),
  createUser: (payload: UserCreate) => post<AdminUser>("/admin/users", payload),
  updateUser: (id: string, payload: UserUpdate) => patch<AdminUser>(`/admin/users/${id}`, payload),
  plantSettings: () => get<PlantSettings>("/admin/plant"),
  updatePlantSettings: (payload: PlantSettingsUpdate) => patch<PlantSettings>("/admin/plant", payload),
  createPlant: (payload: PlantCreate) => post<PlantSettings>("/admin/plants", payload),

  // --- escalations ---------------------------------------------------------
  escalations: (open?: boolean) =>
    get<Escalation[]>(`/escalations${open === undefined ? "" : `?open=${open}`}`),
  acknowledgeEscalation: (id: string) => post<Escalation>(`/escalations/${id}/acknowledge`),

  // --- shift --------------------------------------------------------------
  currentShift: () => get<ShiftSession | null>("/shifts/current"),
  startShift: (shiftId: string, assignments: unknown[]) =>
    post<ShiftSession>("/shifts/start", { shift_id: shiftId, assignments }),
  closureCheck: (id: string) => get<ClosureCheck>(`/shifts/${id}/closure-check`),
  closeShift: (id: string, overrideReason?: string) =>
    post<ShiftSession>(`/shifts/${id}/close`, { override_reason: overrideReason ?? null }),
  shiftHistory: (since?: string) =>
    get<ShiftSession[]>(`/shifts/history${since ? `?since=${since}` : ""}`),

  // --- capture ------------------------------------------------------------
  grid: () => get<StationTile[]>("/grid"),
  openDowntime: () => get<DowntimeEvent[]>("/downtime/open"),
  startDowntime: (stationId: string, reasonCodeId: string | null, note?: string) =>
    post<DowntimeEvent>("/downtime/start", {
      station_id: stationId,
      reason_code_id: reasonCodeId,
      note: note ?? null,
    }),
  closeDowntime: (eventId: string, reasonCodeId?: string) =>
    post<DowntimeEvent>(`/downtime/${eventId}/close`, {
      reason_code_id: reasonCodeId ?? null,
    }),
  recordProduction: (stationId: string, count: number) =>
    post<{ id: string; count: number }>("/production", { station_id: stationId, count }),
  recordQuality: (payload: QualityPayload) => post<QualityResult>("/quality", payload),

  // --- offline sync --------------------------------------------------------
  // Applied in order; each op answers applied, duplicate, held or rejected.
  syncBatch: (deviceId: string, ops: SyncOpIn[]) =>
    post<{ results: SyncResult[] }>("/sync/batch", { device_id: deviceId, ops }),
  holds: (status: HoldStatus = "held") => get<SyncHold[]>(`/sync/holds?status=${status}`),
  applyHold: (id: string, note: string) => post<SyncHold>(`/sync/holds/${id}/apply`, { note }),
  discardHold: (id: string, note: string) => post<SyncHold>(`/sync/holds/${id}/discard`, { note }),
  changeJob: (stationId: string, newJobOrderId: string, operatorId?: string) =>
    post<{ changeover_event_id: string; message: string }>("/change-job", {
      station_id: stationId,
      new_job_order_id: newJobOrderId,
      operator_id: operatorId ?? null,
    }),

  // --- shift entries and corrections --------------------------------------
  shiftEntries: (shiftSessionId: string) => get<ShiftEntries>(`/shifts/${shiftSessionId}/entries`),
  correctDowntime: (id: string, payload: DowntimeCorrection) =>
    post<DowntimeEvent>(`/corrections/downtime/${id}`, payload),
  correctProduction: (id: string, payload: { count: number; reason: string }) =>
    post<Omit<ShiftEntryProduction, "station_code" | "source"> & { covers_from: string | null }>(
      `/corrections/production/${id}`,
      payload,
    ),
  correctQuality: (id: string, payload: QualityCorrection) =>
    post<QualityResult>(`/corrections/quality/${id}`, payload),
  auditLog: (entityType: AuditEntityType, entityId: string, limit = 50) =>
    get<AuditEntry[]>(`/audit-log?entity_type=${entityType}&entity_id=${entityId}&limit=${limit}`),

  // --- photos --------------------------------------------------------------
  uploadAttachment: (file: File, entityType: "action" | "memory", entityId: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("entity_type", entityType);
    form.append("entity_id", entityId);
    return upload<Attachment>("/attachments", form);
  },
  attachmentBlob: blob,

  // --- notifications -------------------------------------------------------
  pushPublicKey: () => get<PushPublicKey>("/push/public-key"),
  subscribePush: (payload: { endpoint: string; keys: { p256dh: string; auth: string }; user_agent?: string }) =>
    post<{ id: string }>("/push/subscriptions", payload),
  unsubscribePush: (endpoint: string) =>
    request<void>("/push/subscriptions", { method: "DELETE", body: JSON.stringify({ endpoint }) }),
  notifications: (filters: { kind?: string; status?: string; limit?: number } = {}) =>
    get<NotificationLog[]>(
      `/notifications${query(
        filters.kind ? `kind=${filters.kind}` : "",
        filters.status ? `status=${filters.status}` : "",
        `limit=${filters.limit ?? 100}`,
      )}`,
    ),
  notificationChannels: () => get<NotificationChannels>("/notifications/channels"),

  // --- reports ------------------------------------------------------------
  ownerHome: (asOf?: string) =>
    get<OwnerHome>(`/reports/owner-home${asOf ? `?as_of=${asOf}` : ""}`),
  losses: (start: string, end: string) =>
    get<LossReport>(`/reports/losses?start=${start}&end=${end}`),
  meetingPack: (businessDate?: string) =>
    get<MeetingPack>(`/reports/meeting${businessDate ? `?business_date=${businessDate}` : ""}`),
  kpis: (shiftSessionId: string) => get<ShiftKpis>(`/reports/kpis/${shiftSessionId}`),
  trend: (days = 28) =>
    get<{
      loss: Array<{ date: string; recoverable: number; structural: number; minutes: number }>;
      recovered: Array<{ week: string; saving: number; cumulative: number }>;
    }>(`/reports/trend?days=${days}`),
  shiftReport: (shiftSessionId: string) =>
    get<{ text: string; parts: number; target: number; oee: number; top_losses: unknown[] }>(
      `/reports/shift-report/${shiftSessionId}`,
    ),

  // --- actions ------------------------------------------------------------
  actions: (state?: string) => get<Action[]>(`/actions${state ? `?state=${state}` : ""}`),
  action: (id: string) => get<Action>(`/actions/${id}`),
  createAction: (payload: Record<string, unknown>) => post<Action>("/actions", payload),
  updateAction: (id: string, payload: Record<string, unknown>) =>
    patch<Action>(`/actions/${id}`, payload),
  closeAction: (id: string, payload: Record<string, unknown>) =>
    post<Action>(`/actions/${id}/close`, payload),
  rca: (id: string) => get<Rca>(`/actions/${id}/rca`),
  updateRca: (id: string, payload: Record<string, unknown>) =>
    patch<{ status: string }>(`/actions/${id}/rca`, payload),
  memory: (q?: string) =>
    get<MemoryEntry[]>(`/actions/memory${q ? `?q=${encodeURIComponent(q)}` : ""}`),

  // --- AI -----------------------------------------------------------------
  // The briefing is generated at 5:45 AM, not on read. Before then the server
  // answers 404 "No briefing yet" — an expected state, so it comes back as null.
  coachToday: async () => {
    try {
      return await get<CoachBriefing>("/ai/coach/today");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  },
  generateCoach: () => post<CoachBriefing>("/ai/coach/generate"),
  ask: (question: string, language: "en" | "ta" = "en") =>
    post<CopilotAnswer>("/ai/copilot/ask", { question, language }),

  // --- onboarding ---------------------------------------------------------
  readinessQuestions: () =>
    request<ReadinessQuestion[]>("/onboarding/readiness/questions", { auth: false }),
  submitReadiness: (payload: Record<string, unknown>) =>
    request<ReadinessResult>("/onboarding/readiness", {
      method: "POST",
      auth: false,
      body: JSON.stringify(payload),
    }),
  templates: () =>
    get<Array<{ code: string; name: string; required_columns: string[]; optional_columns: string[]; go_live_minimum: boolean }>>(
      "/onboarding/templates",
    ),
  uploadTemplate: (code: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return upload<UploadPreview>(`/onboarding/upload/${code}`, form);
  },
  confirmUpload: (uploadId: string) =>
    post<UploadConfirmResult>("/onboarding/confirm", { upload_id: uploadId }),
  // The default T6 library; codes the plant already has are skipped.
  loadOnboardingDefaults: () => post<OnboardingDefaults>("/onboarding/defaults"),

  // --- Layer 5: plugins ---------------------------------------------------
  // Reads never re-score; scoring is an explicit, role-gated POST.
  compliance: (standard?: string) =>
    get<ComplianceOverview>(`/plugins/compliance${standard ? `?standard=${standard}` : ""}`),
  refreshCompliance: () => post<ComplianceOverview>("/plugins/compliance/refresh"),
  clauseEvidence: (clauseId: string) =>
    get<{ clause: ComplianceOverview["clauses"][number]; evidence: Array<Record<string, unknown>> }>(
      `/plugins/compliance/${clauseId}/evidence`,
    ),
  generateAuditPack: (standard: string, start: string, end: string) =>
    post<AuditPack>(
      `/plugins/compliance/pack?standard=${standard}&period_start=${start}&period_end=${end}`,
    ),

  kaizenIdeas: () => get<KaizenIdea[]>("/plugins/kaizen"),
  submitKaizen: (payload: {
    title: string;
    description: string;
    submitted_by_name?: string;
    station_id?: string | null;
  }) => post<KaizenIdea>("/plugins/kaizen", payload),
  decideKaizen: (
    id: string,
    payload: { accept: boolean; note?: string; owner_name?: string; due_date?: string },
  ) => post<KaizenIdea>(`/plugins/kaizen/${id}/decide`, payload),
  kaizenLeaderboard: () => get<KaizenContributor[]>("/plugins/kaizen/leaderboard"),

  skillMatrix: () => get<SkillMatrix>("/plugins/skills/matrix"),
  setSkill: (payload: { operator_id: string; station_id: string; level: number }) =>
    put<{ id: string; level: number }>("/plugins/skills", payload),
  trainingNeeds: () => get<TrainingNeed[]>("/plugins/skills/needs"),
  refreshTrainingNeeds: () => post<TrainingNeed[]>("/plugins/skills/needs/refresh"),
  raiseTrainingAction: (needId: string) =>
    post<{ action_id: string; reference: string }>(`/plugins/skills/needs/${needId}/action`),

  // --- traceability and stages (§3, §14.2) -------------------------------
  searchLots: (q: string) => get<LotSearchResult[]>(`/trace/search?q=${encodeURIComponent(q)}`),
  traceLot: (lotCode: string) => get<LotTrace>(`/trace/lots/${encodeURIComponent(lotCode)}`),
  wip: () => get<WipLot[]>("/trace/wip"),
  recordStage: (payload: {
    lot_code: string;
    operation_code: string;
    quantity_completed: number;
    quantity_rejected?: number;
    station_id?: string | null;
  }) => post<{ lot_code: string; flow: LotFlow }>("/trace/stages", payload),
  recordDispatch: (payload: { lot_code: string; quantity: number; vehicle_reference?: string }) =>
    post<{ lot_code: string; flow: LotFlow }>("/trace/dispatch", payload),

  // --- role KPI pages (§8) and the monthly report (§4.4) ------------------
  roleKpis: <R extends "executive" | "supervisor" | "quality" | "maintenance">(role: R, days = 7) =>
    get<
      R extends "executive"
        ? ExecutiveKpis
        : R extends "supervisor"
          ? SupervisorKpis
          : R extends "quality"
            ? QualityKpis
            : MaintenanceKpis
    >(`/reports/roles/${role}?days=${days}`),
  monthlyReport: (month?: string) =>
    get<MonthlyReport>(`/reports/monthly${month ? `?month=${month}` : ""}`),
};
