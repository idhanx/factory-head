"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Sheet } from "@/components/ui/sheet";
import type { MasterResource, MasterShapes } from "@/types/api";
import {
  ActiveBadge, DataTable, ListState, Notice, RecordForm, RowButton, Toolbar,
  forEdit, invalidateMaster, toValues, type Column, type FieldSpec, type FormValues,
} from "./shared";
import { useT } from "@/lib/i18n";

/**
 * The seven master tables behind every picker in the app.
 *
 * One component, seven configurations: a station and a defect code are edited
 * the same way, so they should feel the same way. Rows are retired with
 * `is_active`, never deleted — a reason code used on 400 past stops must keep
 * meaning what it meant.
 */

type Row<K extends MasterResource> = MasterShapes[K]["row"];

interface MasterConfig<K extends MasterResource> {
  resource: K;
  title: string;
  noun: string;
  hint: string;
  columns: Column<Row<K>>[];
  fields: FieldSpec[];
  defaults: FormValues;
  name: (row: Row<K>) => string;
}

const SECTIONS = [
  { value: "machining", label: "Machining" },
  { value: "assembly", label: "Assembly" },
  { value: "quality", label: "Quality" },
  { value: "dispatch", label: "Dispatch" },
];

const ROUTES = [
  { value: "maintenance", label: "Maintenance" },
  { value: "stores", label: "Stores" },
  { value: "quality", label: "Quality" },
  { value: "production", label: "Production" },
  { value: "none", label: "No escalation" },
];

const ROLE_TAGS = [
  { value: "maintenance", label: "Maintenance" },
  { value: "stores", label: "Stores" },
  { value: "quality", label: "Quality" },
  { value: "production", label: "Production" },
  { value: "plant_head", label: "Plant head" },
  { value: "owner", label: "Owner" },
];

const muted = (text: string | null | undefined) =>
  text ? text : <span className="text-3">—</span>;

/* Column cells are declared in module-level config objects, so they cannot call
   a hook themselves. Anything with words in it becomes a one-line component
   that can. */
function BottleneckBadge() {
  const t = useT();
  return <Badge variant="warn">{t("Bottleneck")}</Badge>;
}

function MarginBandBadge({ band }: { band: string }) {
  const t = useT();
  return <Badge variant="neutral">{t("Band {0}", { 0: band })}</Badge>;
}

function BreakMinutes({ minutes }: { minutes: number }) {
  const t = useT();
  return <span className="tnum">{t("{0} min", { 0: minutes })}</span>;
}

function EscalationBadge({ level }: { level: number }) {
  const t = useT();
  return <Badge variant="neutral">{t("L{0}", { 0: level })}</Badge>;
}

function RecoverabilityBadge({ recoverability }: { recoverability: string }) {
  const t = useT();
  const recoverable = recoverability === "R";
  return (
    <Badge variant={recoverable ? "recoverable" : "structural"}>
      {recoverable ? t("Recoverable") : t("Structural")}
    </Badge>
  );
}

// ---------------------------------------------------------------------------

export const STATIONS: MasterConfig<"stations"> = {
  resource: "stations",
  title: "Stations",
  noun: "station",
  hint: "Every machine or work point that can stop. The section decides which rate costs its downtime.",
  name: (row) => row.code,
  columns: [
    { header: "Code", cell: (r) => <span className="font-semibold">{r.code}</span> },
    { header: "Name", cell: (r) => r.name },
    { header: "Section", cell: (r) => <span className="capitalize">{r.section}</span> },
    { header: "Line", cell: (r) => muted(r.line) },
    {
      header: "Bottleneck factor",
      cell: (r) => (
        <span className="flex items-center gap-2">
          <span className="tnum">{Number(r.bottleneck_factor).toFixed(2)}</span>
          {r.is_bottleneck ? <BottleneckBadge /> : null}
        </span>
      ),
    },
    { header: "Cost family", cell: (r) => muted(r.cost_family_code) },
    { header: "Status", cell: (r) => <ActiveBadge active={r.is_active} /> },
  ],
  fields: [
    { key: "code", label: "Code", kind: "text", required: true, createOnly: true, placeholder: "CNC-03" },
    { key: "name", label: "Name", kind: "text", required: true, placeholder: "CNC lathe 3" },
    { key: "section", label: "Section", kind: "select", required: true, options: SECTIONS },
    { key: "station_type", label: "Machine type", kind: "text", placeholder: "CNC lathe" },
    { key: "line", label: "Line", kind: "text", placeholder: "Line A" },
    { key: "zone", label: "Zone", kind: "text" },
    {
      key: "bottleneck_factor",
      label: "Bottleneck factor (0–1)",
      kind: "number",
      required: true,
      min: 0,
      max: 1,
      hint: "1.0 for the bottleneck or unknown; 0–0.5 when a downstream buffer absorbs stops.",
    },
    {
      key: "cost_family_code",
      label: "Cost family code",
      kind: "text",
      hint: "Matches a machine rate in Cost rates.",
    },
    { key: "is_bottleneck", label: "This station is the line's bottleneck", kind: "checkbox", wide: true },
  ],
  defaults: {
    code: "", name: "", section: "machining", station_type: "", line: "", zone: "",
    bottleneck_factor: "1", cost_family_code: "", is_bottleneck: false,
  },
};

export const PARTS: MasterConfig<"parts"> = {
  resource: "parts",
  title: "Parts",
  noun: "part",
  hint: "Margins here are owner-confidential: they cost lost output, and are never shown on the floor.",
  name: (row) => row.part_number,
  columns: [
    { header: "Part number", cell: (r) => <span className="font-semibold">{r.part_number}</span> },
    { header: "Description", cell: (r) => muted(r.description) },
    { header: "Customer", cell: (r) => muted(r.customer_name) },
    { header: "Family", cell: (r) => muted(r.part_family) },
    {
      header: "Margin / part",
      cell: (r) =>
        r.contribution_margin_rupees !== null && r.contribution_margin_rupees !== undefined ? (
          <span className="money">₹{Number(r.contribution_margin_rupees).toLocaleString("en-IN")}</span>
        ) : r.margin_band ? (
          <MarginBandBadge band={r.margin_band} />
        ) : (
          <span className="text-3">—</span>
        ),
    },
    { header: "Status", cell: (r) => <ActiveBadge active={r.is_active} /> },
  ],
  fields: [
    { key: "part_number", label: "Part number", kind: "text", required: true, createOnly: true },
    { key: "description", label: "Description", kind: "text" },
    { key: "customer_name", label: "Customer", kind: "text" },
    { key: "part_family", label: "Part family", kind: "text" },
    {
      key: "contribution_margin_rupees",
      label: "Contribution margin per part (₹)",
      kind: "number",
      min: 0,
      hint: "Selling price minus material and direct costs. Leave blank to use the band.",
    },
    {
      key: "margin_band",
      label: "Margin band",
      kind: "select",
      options: [
        { value: "L", label: "L — low" },
        { value: "M", label: "M — medium" },
        { value: "H", label: "H — high" },
      ],
      hint: "When the exact margin is not shared.",
    },
    { key: "material_cost_rupees", label: "Material cost per part (₹)", kind: "number", min: 0, omitIfBlank: true },
  ],
  defaults: {
    part_number: "", description: "", customer_name: "", part_family: "",
    contribution_margin_rupees: "", margin_band: "", material_cost_rupees: "",
  },
};

export const REASON_CODES: MasterConfig<"reason-codes"> = {
  resource: "reason-codes",
  title: "Reason codes",
  noun: "reason code",
  hint: "The list a supervisor taps when a machine stops. Recoverable losses become actions; structural ones are reported, never actioned.",
  name: (row) => row.label,
  columns: [
    { header: "Code", cell: (r) => <span className="font-semibold">{r.code}</span> },
    { header: "Label", cell: (r) => r.label },
    {
      header: "Type",
      cell: (r) => (
        <RecoverabilityBadge recoverability={r.recoverability} />
      ),
    },
    { header: "OEE", cell: (r) => <span className="capitalize">{r.oee_treatment}</span> },
    { header: "Escalates to", cell: (r) => <span className="capitalize">{r.escalation_route}</span> },
    { header: "Order", cell: (r) => <span className="tnum">{r.display_order}</span> },
    { header: "Status", cell: (r) => <ActiveBadge active={r.is_active} /> },
  ],
  fields: [
    { key: "code", label: "Code", kind: "text", required: true, createOnly: true, placeholder: "BRK" },
    { key: "label", label: "Label", kind: "text", required: true, placeholder: "Machine breakdown" },
    { key: "label_ta", label: "Label in Tamil", kind: "text" },
    {
      key: "recoverability",
      label: "Recoverable or structural",
      kind: "select",
      required: true,
      options: [
        { value: "R", label: "Recoverable — someone can fix it" },
        { value: "S", label: "Structural — power cut, no orders, planned" },
      ],
    },
    {
      key: "oee_treatment",
      label: "Counts against OEE as",
      kind: "select",
      required: true,
      options: [
        { value: "unplanned", label: "Unplanned stop" },
        { value: "planned", label: "Planned time (excluded)" },
      ],
    },
    { key: "escalation_route", label: "Escalate to", kind: "select", required: true, options: ROUTES },
    {
      key: "display_order",
      label: "Order on the stop screen",
      kind: "number",
      required: true,
      integer: true,
      min: 0,
      hint: "Lower numbers get the big buttons.",
    },
    { key: "is_breakdown", label: "Counts as a breakdown (MTBF, MTTR)", kind: "checkbox" },
    { key: "safety_flag", label: "Safety issue", kind: "checkbox" },
  ],
  defaults: {
    code: "", label: "", label_ta: "", recoverability: "R", oee_treatment: "unplanned",
    escalation_route: "none", display_order: "10", is_breakdown: false, safety_flag: false,
  },
};

export const DEFECT_CODES: MasterConfig<"defect-codes"> = {
  resource: "defect-codes",
  title: "Defect codes",
  noun: "defect code",
  hint: "What the inspector picks for a rejected or reworked part.",
  name: (row) => row.label,
  columns: [
    { header: "Code", cell: (r) => <span className="font-semibold">{r.code}</span> },
    { header: "Label", cell: (r) => r.label },
    { header: "Part family", cell: (r) => muted(r.part_family) },
    { header: "Order", cell: (r) => <span className="tnum">{r.display_order}</span> },
    { header: "Status", cell: (r) => <ActiveBadge active={r.is_active} /> },
  ],
  fields: [
    { key: "code", label: "Code", kind: "text", required: true, createOnly: true, placeholder: "OD-OS" },
    { key: "label", label: "Label", kind: "text", required: true, placeholder: "Outer diameter oversize" },
    { key: "label_ta", label: "Label in Tamil", kind: "text" },
    { key: "part_family", label: "Part family", kind: "text", hint: "Blank applies to every part." },
    { key: "display_order", label: "Order", kind: "number", required: true, integer: true, min: 0 },
  ],
  defaults: { code: "", label: "", label_ta: "", part_family: "", display_order: "10" },
};

export const OPERATORS: MasterConfig<"operators"> = {
  resource: "operators",
  title: "Operators",
  noun: "operator",
  hint: "Names for job tagging and the skill matrix. Operators do not log in.",
  name: (row) => row.name,
  columns: [
    { header: "Name", cell: (r) => <span className="font-semibold">{r.name}</span> },
    { header: "Employee code", cell: (r) => muted(r.employee_code) },
    { header: "Status", cell: (r) => <ActiveBadge active={r.is_active} /> },
  ],
  fields: [
    { key: "name", label: "Name", kind: "text", required: true },
    { key: "employee_code", label: "Employee code", kind: "text" },
  ],
  defaults: { name: "", employee_code: "" },
};

export const SHIFTS: MasterConfig<"shifts"> = {
  resource: "shifts",
  title: "Shifts",
  noun: "shift",
  hint: "Breaks are planned time: they come off the OEE denominator, not out of availability.",
  name: (row) => row.name,
  columns: [
    { header: "Name", cell: (r) => <span className="font-semibold">{r.name}</span> },
    {
      header: "Hours",
      cell: (r) => (
        <span className="tnum">
          {String(r.start_time).slice(0, 5)}–{String(r.end_time).slice(0, 5)}
        </span>
      ),
    },
    { header: "Break", cell: (r) => <BreakMinutes minutes={r.break_minutes} /> },
    { header: "Weekly off", cell: (r) => muted(r.weekly_off) },
    { header: "Status", cell: (r) => <ActiveBadge active={r.is_active} /> },
  ],
  fields: [
    { key: "name", label: "Name", kind: "text", required: true, placeholder: "Shift A" },
    {
      key: "weekly_off",
      label: "Weekly off",
      kind: "select",
      options: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => ({ value: d, label: d })),
    },
    { key: "start_time", label: "Starts", kind: "time", required: true },
    { key: "end_time", label: "Ends", kind: "time", required: true, hint: "Earlier than the start means it runs past midnight." },
    { key: "break_minutes", label: "Break minutes", kind: "number", required: true, integer: true, min: 0, max: 720 },
    { key: "display_order", label: "Order", kind: "number", required: true, integer: true, min: 0 },
  ],
  defaults: {
    name: "", weekly_off: "", start_time: "06:00", end_time: "14:00", break_minutes: "30", display_order: "1",
  },
};

export const ALERT_RECIPIENTS: MasterConfig<"alert-recipients"> = {
  resource: "alert-recipients",
  title: "Alert recipients",
  noun: "recipient",
  hint: "Who gets the WhatsApp when a stop runs long. Level 1 hears first; level 3 only if nobody acts.",
  name: (row) => row.name,
  columns: [
    { header: "Name", cell: (r) => <span className="font-semibold">{r.name}</span> },
    { header: "Role", cell: (r) => ROLE_TAGS.find((tag) => tag.value === r.role_tag)?.label ?? r.role_tag },
    { header: "WhatsApp", cell: (r) => <span className="tnum">{muted(r.whatsapp_number)}</span> },
    { header: "Level", cell: (r) => <EscalationBadge level={r.escalation_level} /> },
    { header: "Status", cell: (r) => <ActiveBadge active={r.is_active} /> },
  ],
  fields: [
    { key: "name", label: "Name", kind: "text", required: true },
    { key: "role_tag", label: "Role", kind: "select", required: true, options: ROLE_TAGS },
    {
      key: "whatsapp_number",
      label: "WhatsApp number",
      kind: "text",
      placeholder: "+91 98765 43210",
      hint: "With the country code.",
    },
    {
      key: "escalation_level",
      label: "Escalation level",
      kind: "select",
      required: true,
      numeric: true,
      options: [
        { value: "1", label: "Level 1 — first call" },
        { value: "2", label: "Level 2 — if not acted on" },
        { value: "3", label: "Level 3 — last resort" },
      ],
    },
  ],
  defaults: { name: "", role_tag: "maintenance", whatsapp_number: "", escalation_level: "1" },
};

// ---------------------------------------------------------------------------

export function MasterTab<K extends MasterResource>({ config }: { config: MasterConfig<K> }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<Row<K> | "new" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const { resource, noun } = config;

  const list = useQuery({
    queryKey: ["settings", resource, showInactive],
    queryFn: () => api.masterList(resource, showInactive),
  });

  const save = useMutation({
    mutationFn: ({ id, payload }: { id: string | null; payload: Record<string, unknown> }) =>
      id
        ? api.updateMaster(resource, id, payload as Partial<MasterShapes[K]["input"]>)
        : api.createMaster(resource, { ...payload, is_active: true } as MasterShapes[K]["input"]),
    onSuccess: (row, { id }) => {
      invalidateMaster(queryClient, resource);
      setNotice(`${config.name(row as Row<K>)} ${id ? "saved" : "added"}.`);
      setEditing(null);
    },
  });

  const toggle = useMutation({
    mutationFn: (row: Row<K>) =>
      api.updateMaster(resource, row.id, {
        is_active: !row.is_active,
      } as Partial<MasterShapes[K]["input"]>),
    onSuccess: (_updated, row) => {
      invalidateMaster(queryClient, resource);
      setNotice(
        row.is_active
          ? t(
              '{0} deactivated. It no longer appears in pickers; turn on "Show inactive" to bring it back.',
              { 0: config.name(row) },
            )
          : t("{0} is active again.", { 0: config.name(row) }),
      );
    },
    onError: (e: Error, row) =>
      setNotice(t("Could not change {0}: {1}", { 0: config.name(row), 1: e.message })),
  });

  // The server filters too; this keeps a stale cache from showing a retired row.
  const rows = ((list.data ?? []) as Row<K>[]).filter((row) => showInactive || row.is_active !== false);

  return (
    <div>
      <Toolbar
        title={config.title}
        hint={config.hint}
        showInactive={showInactive}
        onShowInactive={setShowInactive}
        addLabel={t("Add {0}", { 0: t(noun) })}
        onAdd={() => {
          save.reset();
          setEditing("new");
        }}
      />
      {notice ? <Notice message={notice} onDismiss={() => setNotice(null)} /> : null}

      <ListState
        loading={list.isLoading}
        error={list.error}
        empty={rows.length === 0}
        emptyTitle={t("No {0} yet", { 0: t(config.title).toLowerCase() })}
        emptyHint={t("Add one here, or upload the onboarding template.")}
        onRetry={() => void list.refetch()}
      >
        <DataTable
          rows={rows}
          columns={config.columns}
          inactive={(row) => row.is_active === false}
          actions={(row) => (
            <>
              <RowButton
                onClick={() => {
                  save.reset();
                  setEditing(row);
                }}
                aria-label={t("Edit {0}", { 0: config.name(row) })}
              >
                {t("Edit")}
              </RowButton>
              <RowButton
                variant="ghost"
                className="border"
                disabled={toggle.isPending}
                onClick={() => toggle.mutate(row)}
                aria-label={
                  row.is_active
                    ? t("Deactivate {0}", { 0: config.name(row) })
                    : t("Activate {0}", { 0: config.name(row) })
                }
              >
                {row.is_active ? t("Deactivate") : t("Activate")}
              </RowButton>
            </>
          )}
        />
      </ListState>

      {editing ? (
        <Sheet
          title={
            editing === "new"
              ? t("Add {0}", { 0: t(noun) })
              : t("Edit {0}", { 0: config.name(editing) })
          }
          onClose={() => setEditing(null)}
        >
          <RecordForm
            idPrefix={`${resource}-form`}
            fields={editing === "new" ? config.fields : forEdit(config.fields)}
            initial={editing === "new" ? config.defaults : { ...config.defaults, ...toValues(editing, config.fields) }}
            submitLabel={editing === "new" ? t("Add {0}", { 0: t(noun) }) : t("Save changes")}
            busy={save.isPending}
            serverError={save.error?.message}
            onSubmit={(payload) => save.mutate({ id: editing === "new" ? null : editing.id, payload })}
            onCancel={() => setEditing(null)}
          />
        </Sheet>
      ) : null}
    </div>
  );
}
