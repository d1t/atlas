"use client";

import { FormEvent, useState } from "react";
import { AppShell } from "../../components/AppShell";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";

export default function ProfilePage() {
  const { user, refreshUser } = useAuth();
  const [fullName, setFullName] = useState(user?.full_name || "");
  const [companyName, setCompanyName] = useState(user?.company_name || "");
  const [title, setTitle] = useState(user?.title || "");
  const [phone, setPhone] = useState(user?.phone || "");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(
    null,
  );

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setMsg(null);
    try {
      const updated = await api.updateMe({
        full_name: fullName,
        company_name: companyName,
        title,
        phone,
      });
      refreshUser(updated);
      setMsg({ kind: "ok", text: "Profile saved." });
    } catch (e) {
      setMsg({ kind: "err", text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <AppShell>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">Profile</h1>
        <p className="text-sm text-gray-400">
          Used to personalise outreach emails, NCNDAs, SPAs and other generated
          documents. The sender block below is injected into every document.
        </p>
      </div>

      <form onSubmit={onSubmit} className="card max-w-xl space-y-4">
        <Field label="Email (read-only)">
          <input
            className="input w-full"
            value={user?.email || ""}
            readOnly
            disabled
          />
        </Field>
        <Field label="Full name" hint="Appears on signatures.">
          <input
            className="input w-full"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="e.g. Jane Smith"
          />
        </Field>
        <Field label="Company name" hint="Appears as the sender's company.">
          <input
            className="input w-full"
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            placeholder="e.g. Northbridge Commodities Ltd"
          />
        </Field>
        <Field label="Title" hint="e.g. Head of Trading, Director.">
          <input
            className="input w-full"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Head of Trading"
          />
        </Field>
        <Field label="Phone" hint="Optional. Appears in email signature.">
          <input
            className="input w-full"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+44 20 1234 5678"
          />
        </Field>

        {msg && (
          <div
            className={`rounded-md px-3 py-2 text-sm ${
              msg.kind === "ok"
                ? "bg-emerald-900/30 text-emerald-300"
                : "bg-red-900/30 text-red-300"
            }`}
          >
            {msg.text}
          </div>
        )}

        <button className="btn-primary" type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save profile"}
        </button>
      </form>
    </AppShell>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-sm">
      <div className="mb-1 text-gray-300">{label}</div>
      {children}
      {hint && <div className="mt-1 text-xs text-gray-500">{hint}</div>}
    </label>
  );
}
