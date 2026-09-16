"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Factory } from "lucide-react";
import { api, loadSession, saveSession } from "@/lib/api";
import { clearApiCache } from "@/lib/offline/push";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorNote, Skeleton } from "@/components/ui/misc";
import type { PlantSettings } from "@/types/api";
import { RecordForm, Toolbar, toValues, type FieldSpec } from "./shared";

/**
 * Plant-wide rules: when a loss becomes an action, how far two counts may
 * disagree, what the plant pays. These thresholds are the difference between
 * a board with six actions a week and one with forty, so each carries a line
 * saying what it does.
 */

const PLANT_FIELDS: FieldSpec[] = [
  { key: "name", label: "Plant name", kind: "text", required: true, wide: true },
  { key: "code", label: "Plant code", kind: "text", readOnly: true, hint: "Used at login. Cannot be changed." },
  { key: "location", label: "Location", kind: "text" },
  { key: "timezone", label: "Time zone", kind: "text", required: true, placeholder: "Asia/Kolkata" },
  {
    key: "costing_mode",
    label: "Costing mode",
    kind: "select",
    required: true,
    options: [
      { value: "basic", label: "Basic — default rates by section" },
      { value: "standard", label: "Standard — the plant's own rates" },
    ],
  },
  {
    key: "action_cost_threshold_rupees",
    label: "Raise an action above (₹)",
    kind: "number",
    required: true,
    min: 0,
    hint: "A single loss costing more than this becomes an action at shift close.",
  },
  {
    key: "action_duration_minutes",
    label: "…or a stop longer than (minutes)",
    kind: "number",
    required: true,
    integer: true,
    min: 0,
  },
  {
    key: "recurrence_count",
    label: "…or repeating this many times",
    kind: "number",
    required: true,
    integer: true,
    min: 1,
  },
  {
    key: "recurrence_window_shifts",
    label: "…within this many shifts",
    kind: "number",
    required: true,
    integer: true,
    min: 1,
  },
  {
    key: "action_due_days",
    label: "Days to fix an auto-created action",
    kind: "number",
    required: true,
    integer: true,
    min: 1,
    max: 60,
    hint: "An action the system raises by itself gets this many days before it shows as overdue.",
  },
  {
    key: "reconciliation_tolerance_parts",
    label: "Count mismatch allowed (parts)",
    kind: "number",
    required: true,
    integer: true,
    min: 0,
    hint: "Between the supervisor's count and the inspector's.",
  },
  {
    key: "reconciliation_tolerance_percent",
    label: "Count mismatch allowed (%)",
    kind: "number",
    required: true,
    min: 0,
    max: 100,
  },
  {
    key: "monthly_fee_rupees",
    label: "Monthly fee (₹)",
    kind: "number",
    required: true,
    min: 0,
    hint: "The denominator of running ROI on the Money screen.",
  },
  { key: "go_live_date", label: "Go-live date", kind: "date" },
  {
    key: "no_blame_until",
    label: "No-blame period ends",
    kind: "date",
    hint: "Until this date, reports never show operator names.",
  },
];

export function PlantTab() {
  const queryClient = useQueryClient();
  const session = loadSession();
  const isAdmin = session?.roles.includes("admin") ?? false;
  const [saved, setSaved] = useState(false);
  // Remount the form after a save so it starts from the server's values.
  const [formKey, setFormKey] = useState(0);

  const plant = useQuery({ queryKey: ["settings", "plant"], queryFn: api.plantSettings });

  const save = useMutation({
    mutationFn: (payload: Record<string, unknown>) => api.updatePlantSettings(payload),
    onSuccess: (data) => {
      queryClient.setQueryData(["settings", "plant"], data);
      void queryClient.invalidateQueries({ queryKey: ["owner-home"] });
      setSaved(true);
      setFormKey((k) => k + 1);
    },
    onError: () => setSaved(false),
  });

  return (
    <div className="space-y-6">
      <Toolbar
        title="Plant"
        hint="Thresholds and money settings for this plant. Changes apply from the next shift close; nothing already closed is re-costed."
      />

      {plant.isLoading ? (
        <Skeleton className="h-96" />
      ) : plant.error ? (
        <div className="space-y-3">
          <ErrorNote message={`Could not load the plant settings: ${plant.error.message}`} />
          <Button size="lg" variant="outline" onClick={() => void plant.refetch()}>
            Try again
          </Button>
        </div>
      ) : plant.data ? (
        <Card>
          <CardContent className="pt-5">
            {saved ? (
              <p role="status" className="surface-2 rise mb-4 rounded-xl border px-4 py-2 text-sm">
                Plant settings saved.
              </p>
            ) : null}
            <RecordForm
              key={formKey}
              idPrefix="plant"
              fields={PLANT_FIELDS}
              initial={toValues(plant.data, PLANT_FIELDS)}
              submitLabel="Save plant settings"
              busy={save.isPending}
              serverError={save.error?.message}
              onSubmit={(payload) => {
                setSaved(false);
                save.mutate(payload);
              }}
              onCancel={() => {
                setSaved(false);
                save.reset();
                setFormKey((k) => k + 1);
              }}
            />
          </CardContent>
        </Card>
      ) : null}

      {isAdmin ? <CreatePlant /> : null}
    </div>
  );
}

const NEW_PLANT_FIELDS: FieldSpec[] = [
  { key: "name", label: "Plant name", kind: "text", required: true },
  { key: "code", label: "Plant code", kind: "text", required: true, placeholder: "HSR-01", hint: "Short, used at login. Cannot be changed later." },
  { key: "location", label: "Location", kind: "text" },
  { key: "timezone", label: "Time zone", kind: "text", placeholder: "Asia/Kolkata" },
  {
    key: "costing_mode",
    label: "Costing mode",
    kind: "select",
    required: true,
    options: [
      { value: "basic", label: "Basic" },
      { value: "standard", label: "Standard" },
    ],
  },
  { key: "owner_full_name", label: "Owner's name", kind: "text", required: true },
  { key: "owner_email", label: "Owner's email", kind: "text" },
  { key: "owner_phone", label: "Owner's phone", kind: "text" },
  { key: "owner_password", label: "Owner's password", kind: "password", required: true, min: 8, hint: "At least 8 characters." },
];

/** Admin only. The caller becomes admin of the new plant and can switch to it. */
function CreatePlant() {
  const [open, setOpen] = useState(false);
  const [created, setCreated] = useState<PlantSettings | null>(null);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [formKey, setFormKey] = useState(0);

  const create = useMutation({
    mutationFn: (p: Record<string, unknown>) =>
      api.createPlant({
        name: String(p.name),
        code: String(p.code),
        location: (p.location as string | null) ?? null,
        // The API defaults the time zone; it does not accept null.
        ...(p.timezone ? { timezone: String(p.timezone) } : {}),
        costing_mode: p.costing_mode as "basic" | "standard",
        owner: {
          full_name: String(p.owner_full_name),
          email: (p.owner_email as string | null) ?? null,
          phone: (p.owner_phone as string | null) ?? null,
          password: String(p.owner_password),
        },
      }),
    onSuccess: (plant) => {
      setCreated(plant);
      setOpen(false);
      setFormKey((k) => k + 1);
    },
  });

  const switchTo = useMutation({
    mutationFn: async (plantId: string) => {
      const session = loadSession();
      if (!session) throw new Error("Your session has ended. Log in again.");
      return api.switchPlant(session.refresh_token, plantId);
    },
    onSuccess: (next) => {
      // The offline copies of the pick-lists belong to the plant being left.
      clearApiCache();
      saveSession(next);
      window.location.href = "/";
    },
  });

  return (
    <Card>
      <CardHeader className="flex-wrap justify-between">
        <CardTitle className="flex items-center gap-1.5">
          <Factory className="size-3.5" />
          Create a plant · admin
        </CardTitle>
        {!open ? (
          <Button size="lg" variant="outline" onClick={() => setOpen(true)}>
            New plant
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4">
        {created ? (
          <div role="status" className="surface-2 rise flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 text-sm">
            <span>
              <span className="font-semibold">{created.name}</span> ({created.code}) created. You are its admin.
            </span>
            <Button size="lg" disabled={switchTo.isPending} onClick={() => switchTo.mutate(created.id)}>
              {switchTo.isPending ? "Switching…" : "Switch to it"}
            </Button>
          </div>
        ) : null}
        {switchTo.error ? <ErrorNote message={`Could not switch plant: ${switchTo.error.message}`} /> : null}

        {open ? (
          <RecordForm
            key={formKey}
            idPrefix="new-plant"
            fields={NEW_PLANT_FIELDS}
            initial={{ costing_mode: "basic", timezone: "Asia/Kolkata" }}
            submitLabel="Create plant"
            busy={create.isPending}
            serverError={invalid ?? create.error?.message}
            onSubmit={(payload) => {
              if (!payload.owner_email && !payload.owner_phone) {
                setInvalid("Give the owner an email or a phone number to log in with.");
                return;
              }
              setInvalid(null);
              create.mutate(payload);
            }}
            onCancel={() => {
              setOpen(false);
              setInvalid(null);
              create.reset();
            }}
          />
        ) : (
          <p className="text-3 text-sm">
            Sets up an empty plant with its first owner. Masters come next, from the onboarding templates.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
