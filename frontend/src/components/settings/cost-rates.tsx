"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label, Select } from "@/components/ui/input";
import { ErrorNote } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import type { CostRate, CostRateInput } from "@/types/api";
import { DataTable, ListState, Notice, Toolbar } from "./shared";

/**
 * Effective-dated cost rates.
 *
 * A rate is never edited — a new one is added from a date. Correcting a machine
 * rate in week six must not quietly turn yesterday's ₹9,380 into ₹11,200, or move
 * a Recovered Profit baseline out from under its own action.
 */

const SCOPES: Array<{ value: CostRate["scope"]; label: string; hint: string }> = [
  { value: "cost_family", label: "Machine family", hint: "Machine rate for every station with this cost family code." },
  { value: "section", label: "Section", hint: "Labour rate for everyone working in a section." },
  { value: "plant", label: "Whole plant", hint: "Overhead spread across every running hour." },
];

const SECTIONS = ["machining", "assembly", "quality", "dispatch"];

const today = () => new Date().toISOString().slice(0, 10);

function rupees(value: number) {
  return <span className="money">₹{Number(value).toLocaleString("en-IN")}</span>;
}

/** The newest rate already in force for each scope and reference. */
function currentIds(rates: CostRate[]): Set<string> {
  const now = today();
  const best = new Map<string, CostRate>();
  for (const rate of rates) {
    if (rate.effective_from > now) continue;
    const key = `${rate.scope}:${rate.reference}`;
    const held = best.get(key);
    if (!held || rate.effective_from > held.effective_from) best.set(key, rate);
  }
  return new Set([...best.values()].map((r) => r.id));
}

export function CostRatesTab() {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const rates = useQuery({ queryKey: ["settings", "cost-rates"], queryFn: api.costRates });

  const rows = [...(rates.data ?? [])].sort(
    (a, b) =>
      a.scope.localeCompare(b.scope) ||
      a.reference.localeCompare(b.reference) ||
      b.effective_from.localeCompare(a.effective_from),
  );
  const inUse = currentIds(rows);

  return (
    <div>
      <Toolbar
        title="Cost rates"
        hint={
          <>
            Rates are dated. Adding a rate applies it from its start date onwards —{" "}
            <strong>past stops keep the rate they were costed with</strong>, so last month&apos;s
            figures and every action&apos;s baseline stay exactly as they were.
          </>
        }
        addLabel="Add rate"
        onAdd={() => setAdding(true)}
      />
      {notice ? <Notice message={notice} onDismiss={() => setNotice(null)} /> : null}

      <ListState
        loading={rates.isLoading}
        error={rates.error}
        empty={rows.length === 0}
        emptyTitle="No cost rates yet"
        emptyHint="Until rates are added, downtime is costed with the default rates for each section."
        onRetry={() => void rates.refetch()}
      >
        <DataTable
          rows={rows}
          inactive={(r) => !inUse.has(r.id) && r.effective_from <= today()}
          columns={[
            { header: "Applies to", cell: (r) => <span className="font-semibold">{SCOPES.find((s) => s.value === r.scope)?.label ?? r.scope}</span> },
            { header: "Reference", cell: (r) => <span className="capitalize">{r.reference}</span> },
            { header: "Machine ₹/h", cell: (r) => rupees(r.machine_rate_rupees) },
            { header: "Labour ₹/h", cell: (r) => rupees(r.labour_rate_rupees) },
            { header: "Overhead ₹/h", cell: (r) => rupees(r.overhead_rate_rupees) },
            { header: "Source", cell: (r) => <span className="capitalize">{r.source}</span> },
            {
              header: "From",
              cell: (r) => (
                <span className="flex items-center gap-2 whitespace-nowrap">
                  <span className="tnum">{r.effective_from}</span>
                  {inUse.has(r.id) ? (
                    <Badge variant="recovered">In use</Badge>
                  ) : r.effective_from > today() ? (
                    <Badge variant="accent">Upcoming</Badge>
                  ) : (
                    <Badge variant="neutral">Replaced</Badge>
                  )}
                </span>
              ),
            },
          ]}
        />
      </ListState>

      {adding ? (
        <AddRateSheet
          onClose={() => setAdding(false)}
          onSaved={(rate) => {
            void queryClient.invalidateQueries({ queryKey: ["settings", "cost-rates"] });
            setAdding(false);
            setNotice(`Rate for ${rate.reference} added from ${rate.effective_from}.`);
          }}
        />
      ) : null}
    </div>
  );
}

function AddRateSheet({ onClose, onSaved }: { onClose: () => void; onSaved: (rate: CostRate) => void }) {
  const [scope, setScope] = useState<CostRate["scope"]>("cost_family");
  const [reference, setReference] = useState("");
  const [machine, setMachine] = useState("");
  const [labour, setLabour] = useState("");
  const [overhead, setOverhead] = useState("");
  const [source, setSource] = useState<CostRate["source"]>("measured");
  const [from, setFrom] = useState(today());
  const [invalid, setInvalid] = useState<string | null>(null);

  // Suggest the cost family codes the stations already use.
  const stations = useQuery({
    queryKey: ["settings", "stations", false],
    queryFn: () => api.masterList("stations", false),
    enabled: scope === "cost_family",
  });
  const families = Array.from(
    new Set((stations.data ?? []).map((s) => s.cost_family_code).filter((c): c is string => Boolean(c))),
  );

  const save = useMutation({
    mutationFn: (payload: CostRateInput) => api.createCostRate(payload),
    onSuccess: onSaved,
  });

  const money = (value: string) => (value.trim() === "" ? 0 : Number(value));

  return (
    <Sheet title="Add a cost rate" subtitle="Applies from its start date. Earlier events are not re-costed." onClose={onClose}>
      <form
        noValidate
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          const ref = scope === "plant" ? "plant" : reference.trim();
          if (!ref) return setInvalid(scope === "section" ? "Pick the section." : "Enter the machine family code.");
          const values = [machine, labour, overhead].map(money);
          if (values.some((v) => !Number.isFinite(v) || v < 0)) return setInvalid("Rates must be numbers of 0 or more.");
          if (values.every((v) => v === 0)) return setInvalid("Enter at least one rate above 0.");
          if (!from) return setInvalid("Pick the date the rate starts.");
          setInvalid(null);
          save.mutate({
            scope,
            reference: ref,
            machine_rate_rupees: values[0],
            labour_rate_rupees: values[1],
            overhead_rate_rupees: values[2],
            source,
            effective_from: from,
          });
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="rate-scope">Applies to</Label>
            <Select
              id="rate-scope"
              value={scope}
              onChange={(e) => {
                setScope(e.target.value as CostRate["scope"]);
                setReference("");
              }}
            >
              {SCOPES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </Select>
            <p className="text-3 mt-1 text-xs">{SCOPES.find((s) => s.value === scope)?.hint}</p>
          </div>

          {scope === "section" ? (
            <div>
              <Label htmlFor="rate-reference">Section</Label>
              <Select id="rate-reference" value={reference} onChange={(e) => setReference(e.target.value)}>
                <option value="">Select…</option>
                {SECTIONS.map((s) => (
                  <option key={s} value={s} className="capitalize">{s[0].toUpperCase() + s.slice(1)}</option>
                ))}
              </Select>
            </div>
          ) : scope === "cost_family" ? (
            <div>
              <Label htmlFor="rate-reference">Machine family code</Label>
              <Input
                id="rate-reference"
                list="rate-families"
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                placeholder={families[0] ?? "CNC-LATHE"}
              />
              <datalist id="rate-families">
                {families.map((f) => <option key={f} value={f} />)}
              </datalist>
            </div>
          ) : (
            <div>
              <Label htmlFor="rate-reference">Reference</Label>
              <Input id="rate-reference" value="plant" readOnly disabled />
            </div>
          )}

          <div>
            <Label htmlFor="rate-machine">Machine rate (₹ per hour)</Label>
            <Input id="rate-machine" inputMode="decimal" value={machine} onChange={(e) => setMachine(e.target.value)} placeholder="0" />
          </div>
          <div>
            <Label htmlFor="rate-labour">Labour rate (₹ per hour)</Label>
            <Input id="rate-labour" inputMode="decimal" value={labour} onChange={(e) => setLabour(e.target.value)} placeholder="0" />
          </div>
          <div>
            <Label htmlFor="rate-overhead">Overhead rate (₹ per hour)</Label>
            <Input id="rate-overhead" inputMode="decimal" value={overhead} onChange={(e) => setOverhead(e.target.value)} placeholder="0" />
          </div>
          <div>
            <Label htmlFor="rate-source">Where the number came from</Label>
            <Select id="rate-source" value={source} onChange={(e) => setSource(e.target.value as CostRate["source"])}>
              <option value="measured">Measured — from the plant&apos;s own accounts</option>
              <option value="derived">Derived — worked out from other figures</option>
              <option value="default">Default — an industry estimate</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="rate-from">Starts on</Label>
            <Input id="rate-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
        </div>

        {invalid ? <ErrorNote message={invalid} /> : null}
        {save.error ? <ErrorNote message={save.error.message} /> : null}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="lg" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Add rate"}
          </Button>
          <Button type="button" size="lg" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
