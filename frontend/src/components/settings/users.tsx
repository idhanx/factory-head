"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { api, loadSession } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { ErrorNote } from "@/components/ui/misc";
import { Sheet } from "@/components/ui/sheet";
import type { AdminUser, Role } from "@/types/api";
import { DataTable, ListState, Notice, RowButton, Toolbar } from "./shared";
import { useLanguage, useT } from "@/lib/i18n";

/**
 * People who log in to this plant.
 *
 * The grant rules mirror the API so the form never offers what the server will
 * refuse: an owner hands out the plant roles; only an admin creates consultants
 * and admins. Nobody can take away their own owner or admin role here — the
 * last owner locking themselves out is a support call, not a setting.
 */

const ROLE_INFO: Record<Role, { label: string; hint: string }> = {
  owner: { label: "Owner", hint: "Money, settings, every screen" },
  supervisor: { label: "Supervisor", hint: "Runs shifts, logs stops and counts" },
  quality: { label: "Quality", hint: "Inspection entry and quality KPIs" },
  maintenance: { label: "Maintenance", hint: "Breakdowns and their actions" },
  stores: { label: "Stores", hint: "Material stops and their actions" },
  consultant: { label: "Consultant", hint: "Onboarding and reviews across plants" },
  admin: { label: "Admin", hint: "Everything, including creating plants" },
};

const PLANT_ROLES: Role[] = ["owner", "supervisor", "quality", "maintenance", "stores"];
const ADMIN_ONLY_ROLES: Role[] = ["consultant", "admin"];
const PROTECTED_SELF_ROLES: Role[] = ["owner", "admin"];

function FormatLogin({ iso }: { iso: string | null }) {
  const t = useT();
  const { locale } = useLanguage();
  if (!iso) return <span className="text-3">{t("Never")}</span>;
  return (
    <>
      {new Date(iso).toLocaleString(locale, {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })}
    </>
  );
}

export function UsersTab() {
  const t = useT();
  const queryClient = useQueryClient();
  const session = loadSession();
  const isAdmin = session?.roles.includes("admin") ?? false;
  const grantable = isAdmin ? [...PLANT_ROLES, ...ADMIN_ONLY_ROLES] : PLANT_ROLES;

  const [showInactive, setShowInactive] = useState(false);
  const [editing, setEditing] = useState<AdminUser | "new" | null>(null);
  const [resetting, setResetting] = useState<AdminUser | null>(null);
  const [removing, setRemoving] = useState<AdminUser | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const users = useQuery({ queryKey: ["settings", "users"], queryFn: api.users });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["settings", "users"] });

  const removeAccess = useMutation({
    mutationFn: (user: AdminUser) => api.updateUser(user.id, { roles: [] }),
    onSuccess: (_u, user) => {
      refresh();
      setRemoving(null);
      setNotice(t("{0} can no longer open this plant.", { 0: user.full_name }));
    },
  });

  // A user with no roles here has had access removed; they are "inactive" to this plant.
  const hasAccess = (u: AdminUser) => u.is_active && u.roles.length > 0;
  const rows = (users.data ?? []).filter((u) => showInactive || hasAccess(u));

  return (
    <div>
      <Toolbar
        title={t("Users")}
        hint={t("Everyone who logs in to this plant, and what they can see. Operators are not users — they are names in the Operators tab.")}
        showInactive={showInactive}
        onShowInactive={setShowInactive}
        inactiveLabel="Show people without access"
        addLabel="Add user"
        onAdd={() => setEditing("new")}
      />
      {notice ? <Notice message={notice} onDismiss={() => setNotice(null)} /> : null}

      <ListState
        loading={users.isLoading}
        error={users.error}
        empty={rows.length === 0}
        emptyTitle="No users to show"
        onRetry={() => void users.refetch()}
      >
        <DataTable
          rows={rows}
          inactive={(u) => !hasAccess(u)}
          columns={[
            {
              header: "Name",
              cell: (u) => (
                <span className="font-semibold">
                  {u.full_name}
                  {u.id === session?.user_id ? <span className="text-3 font-normal"> {t("(you)")}</span> : null}
                </span>
              ),
            },
            {
              header: "Login",
              cell: (u) => (
                <span className="text-2">
                  {[u.email, u.phone].filter(Boolean).join(" · ") || <span className="text-3">—</span>}
                </span>
              ),
            },
            {
              header: "Roles",
              cell: (u) =>
                u.roles.length ? (
                  <span className="flex flex-wrap gap-1">
                    {u.roles.map((r) => (
                      <Badge key={r} variant={r === "owner" || r === "admin" ? "accent" : "neutral"}>
                        {t(ROLE_INFO[r]?.label ?? r)}
                      </Badge>
                    ))}
                  </span>
                ) : (
                  <Badge variant="neutral">{t("No access")}</Badge>
                ),
            },
            { header: "Last login", cell: (u) => <span className="whitespace-nowrap"><FormatLogin iso={u.last_login_at} /></span> },
          ]}
          actions={(u) => (
            <>
              <RowButton onClick={() => setEditing(u)} aria-label={t("Edit {0}", { 0: u.full_name })}>
                {t("Edit")}
              </RowButton>
              <RowButton onClick={() => setResetting(u)} aria-label={t("Reset password for {0}", { 0: u.full_name })}>
                <KeyRound className="size-3.5" />
                {t("Password")}
              </RowButton>
              {u.id !== session?.user_id && u.roles.length > 0 ? (
                <RowButton
                  variant="ghost"
                  className="border text-[var(--color-loss)]"
                  onClick={() => {
                    removeAccess.reset();
                    setRemoving(u);
                  }}
                  aria-label={t("Remove access for {0}", { 0: u.full_name })}
                >
                  {t("Remove access")}
                </RowButton>
              ) : null}
            </>
          )}
        />
      </ListState>

      {editing ? (
        <UserSheet
          user={editing === "new" ? null : editing}
          grantable={grantable}
          selfId={session?.user_id ?? null}
          onClose={() => setEditing(null)}
          onSaved={(user, created) => {
            refresh();
            setEditing(null);
            setNotice(created ? t("{0} added.", { 0: user.full_name }) : t("{0} saved.", { 0: user.full_name }));
          }}
        />
      ) : null}

      {resetting ? (
        <PasswordSheet
          user={resetting}
          onClose={() => setResetting(null)}
          onSaved={() => {
            setResetting(null);
            setNotice(t("Password changed for {0}. Tell them in person, not on WhatsApp.", { 0: resetting.full_name }));
          }}
        />
      ) : null}

      {removing ? (
        <Sheet title={t("Remove {0}'s access?", { 0: removing.full_name })} onClose={() => setRemoving(null)}>
          <p className="text-2 text-sm leading-relaxed">
            {t("They will no longer be able to open this plant. Their name stays on everything they recorded. You can give access back later by editing their roles.")}
          </p>
          {removeAccess.error ? (
            <div className="mt-3">
              <ErrorNote message={removeAccess.error.message} />
            </div>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              size="lg"
              variant="stop"
              disabled={removeAccess.isPending}
              onClick={() => removeAccess.mutate(removing)}
            >
              {removeAccess.isPending ? t("Removing…") : t("Yes, remove access")}
            </Button>
            <Button size="lg" variant="outline" onClick={() => setRemoving(null)}>
              {t("Keep access")}
            </Button>
          </div>
        </Sheet>
      ) : null}
    </div>
  );
}

function UserSheet({
  user,
  grantable,
  selfId,
  onClose,
  onSaved,
}: {
  user: AdminUser | null;
  grantable: Role[];
  selfId: string | null;
  onClose: () => void;
  onSaved: (user: AdminUser, created: boolean) => void;
}) {
  const t = useT();
  const creating = user === null;
  const isSelf = user !== null && user.id === selfId;
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [password, setPassword] = useState("");
  const [roles, setRoles] = useState<Role[]>(user?.roles ?? []);
  const [invalid, setInvalid] = useState<string | null>(null);

  // Roles this person already holds that the current user may not grant stay
  // as they are: hidden from the checkboxes, kept in the payload.
  const untouchable = (user?.roles ?? []).filter((r) => !grantable.includes(r));

  const save = useMutation({
    mutationFn: () => {
      const trimmedEmail = email.trim() || null;
      const trimmedPhone = phone.trim() || null;
      return creating
        ? api.createUser({ full_name: fullName.trim(), email: trimmedEmail, phone: trimmedPhone, password, roles })
        : api.updateUser(user.id, {
            full_name: fullName.trim(),
            email: trimmedEmail,
            phone: trimmedPhone,
            roles: Array.from(new Set([...roles, ...untouchable])),
          });
    },
    onSuccess: (saved) => onSaved(saved, creating),
  });

  function toggleRole(role: Role, checked: boolean) {
    setRoles((prev) => (checked ? [...prev, role] : prev.filter((r) => r !== role)));
  }

  return (
    <Sheet
      title={creating ? t("Add user") : t("Edit {0}", { 0: user.full_name })}
      subtitle={creating ? t("If someone with this email or phone already has a login, they are added to this plant and keep their own password.") : undefined}
      onClose={onClose}
    >
      <form
        noValidate
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!fullName.trim()) return setInvalid(t("Enter the person's name."));
          if (!email.trim() && !phone.trim()) return setInvalid(t("Enter an email or a phone number — it is how they log in."));
          if (email.trim() && !/^\S+@\S+\.\S+$/.test(email.trim())) return setInvalid(t("That email address does not look right."));
          if (creating && password.length < 8) return setInvalid(t("The password must be at least 8 characters."));
          if (roles.length + untouchable.length === 0) return setInvalid(t("Pick at least one role."));
          setInvalid(null);
          save.mutate();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <Label htmlFor="user-name">{t("Full name")}</Label>
            <Input id="user-name" value={fullName} onChange={(e) => setFullName(e.target.value)} autoFocus />
          </div>
          <div>
            <Label htmlFor="user-email">{t("Email")}</Label>
            <Input id="user-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="user-phone">{t("Phone")}</Label>
            <Input id="user-phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" />
          </div>
          {creating ? (
            <div className="sm:col-span-2">
              <Label htmlFor="user-password">{t("Password")}</Label>
              <Input
                id="user-password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <p className="text-3 mt-1 text-xs">{t("At least 8 characters.")}</p>
            </div>
          ) : null}
        </div>

        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">{t("Roles in this plant")}</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {grantable.map((role) => {
              const locked = isSelf && PROTECTED_SELF_ROLES.includes(role) && (user?.roles ?? []).includes(role);
              const id = `user-role-${role}`;
              return (
                <label
                  key={role}
                  htmlFor={id}
                  className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg border px-3 py-2"
                >
                  <input
                    id={id}
                    type="checkbox"
                    className="size-5"
                    checked={roles.includes(role)}
                    disabled={locked}
                    onChange={(e) => toggleRole(role, e.target.checked)}
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{t(ROLE_INFO[role].label)}</span>
                    <span className="text-3 block text-xs">
                      {locked ? t("You cannot remove your own role") : t(ROLE_INFO[role].hint)}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
          {untouchable.length ? (
            <p className="text-3 mt-2 text-xs">
              {t("Also holds {0} — only an admin can change that.", { 0: untouchable.map((r) => t(ROLE_INFO[r]?.label ?? r)).join(", ") })}
            </p>
          ) : null}
        </fieldset>

        {invalid ? <ErrorNote message={invalid} /> : null}
        {save.error ? <ErrorNote message={save.error.message} /> : null}

        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="lg" disabled={save.isPending}>
            {save.isPending ? t("Saving…") : creating ? t("Add user") : t("Save changes")}
          </Button>
          <Button type="button" size="lg" variant="ghost" onClick={onClose}>
            {t("Cancel")}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}

function PasswordSheet({
  user,
  onClose,
  onSaved,
}: {
  user: AdminUser;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => api.updateUser(user.id, { password }),
    onSuccess: onSaved,
  });

  return (
    <Sheet title={t("Reset password — {0}", { 0: user.full_name })} onClose={onClose}>
      <form
        noValidate
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (password.length < 8) return setInvalid(t("The password must be at least 8 characters."));
          if (password !== confirm) return setInvalid(t("The two passwords do not match."));
          setInvalid(null);
          save.mutate();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="reset-password">{t("New password")}</Label>
            <Input
              id="reset-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
            />
          </div>
          <div>
            <Label htmlFor="reset-confirm">{t("Type it again")}</Label>
            <Input
              id="reset-confirm"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </div>
        </div>
        {invalid ? <ErrorNote message={invalid} /> : null}
        {save.error ? <ErrorNote message={save.error.message} /> : null}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" size="lg" disabled={save.isPending}>
            {save.isPending ? t("Saving…") : t("Set password")}
          </Button>
          <Button type="button" size="lg" variant="ghost" onClick={onClose}>
            {t("Cancel")}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
