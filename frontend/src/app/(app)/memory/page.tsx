"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, Lightbulb, Search, Wrench } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Empty, Skeleton } from "@/components/ui/misc";

/**
 * Factory Memory (Layer 4).
 *
 *   "Every plant slowly loses its knowledge. An experienced engineer leaves,
 *    and with him go the tool settings, the machine quirks, the supplier
 *    issues, the root causes of old problems."
 *
 * Each entry cost sixty seconds at the moment of a fix. Fifty of them is a
 * private repair playbook that survives any resignation — and two years of them
 * cannot be copied by a competitor at any price, because they would need the
 * plant's two years of data to match it.
 */
export default function Memory() {
  const [query, setQuery] = useState("");
  const entries = useQuery({
    queryKey: ["memory", query],
    queryFn: () => api.memory(query || undefined),
  });

  const results = entries.data ?? [];

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="rise">
        <p className="label mb-1.5 flex items-center gap-1.5">
          <BookOpen className="size-3" />
          Layer 4
        </p>
        <h1 className="display text-[1.75rem] font-bold">Factory Memory</h1>
        <p className="text-2 mt-1 text-sm">
          Every fix this plant has recorded. Have we seen this before?
        </p>
      </header>

      <div className="rise relative" style={{ animationDelay: "60ms" }}>
        <Search className="text-3 pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2" />
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="rust, insert, coolant, staging…"
          aria-label="Search Factory Memory"
          className="elev-2 h-13 pl-10 text-[0.9375rem]"
        />
        {results.length ? (
          <span className="text-3 absolute right-3.5 top-1/2 -translate-y-1/2 text-xs tnum">
            {results.length}
          </span>
        ) : null}
      </div>

      {entries.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      ) : results.length === 0 ? (
        <Empty
          icon={<BookOpen className="size-6" />}
          title={query ? "Nothing recorded like that" : "Memory is still filling"}
          hint="Entries are captured automatically when someone closes an action — four questions, sixty seconds. Memory with three weeks of history is thin; with two years it is the plant's deepest asset."
        />
      ) : (
        <div className="space-y-3">
          {results.map((entry, index) => (
            <Card
              key={entry.id}
              className="rise"
              style={{ animationDelay: `${Math.min(index * 50, 300)}ms` }}
            >
              <CardContent className="pt-5">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <p className="font-semibold">{entry.problem_summary ?? "Recorded fix"}</p>
                  <div className="flex items-center gap-2">
                    {entry.station_code ? (
                      <Badge variant="neutral">{entry.station_code}</Badge>
                    ) : null}
                    <span className="text-3 text-xs tnum">{entry.occurred_on}</span>
                  </div>
                </div>

                <dl className="mt-3.5 space-y-3 text-sm">
                  <div className="flex gap-2.5">
                    <Wrench className="text-3 mt-0.5 size-3.5 shrink-0" />
                    <div>
                      <dt className="label mb-0.5">What fixed it</dt>
                      <dd className="leading-relaxed">{entry.what_fixed_it}</dd>
                    </div>
                  </div>
                  {entry.what_was_replaced ? (
                    <div className="pl-6">
                      <dt className="label mb-0.5">Replaced or changed</dt>
                      <dd className="text-2 leading-relaxed">{entry.what_was_replaced}</dd>
                    </div>
                  ) : null}
                  {entry.check_first_next_time ? (
                    <div className="flex gap-2.5 rounded-xl border bg-[var(--color-warn-dim)] p-3">
                      <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-[var(--color-warn)]" />
                      <div>
                        <dt className="label mb-0.5 text-[var(--color-warn)]">
                          Check this first next time
                        </dt>
                        <dd className="font-medium leading-relaxed">
                          {entry.check_first_next_time}
                        </dd>
                      </div>
                    </div>
                  ) : null}
                </dl>

                {entry.fixed_by_name ? (
                  <p className="text-3 mt-3.5 text-xs">Recorded by {entry.fixed_by_name}</p>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
