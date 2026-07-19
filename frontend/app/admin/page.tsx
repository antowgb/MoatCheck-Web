"use client";

import { Check, KeyRound } from "lucide-react";
import { useEffect, useState } from "react";
import AddStockForm from "@/components/AddStockForm";
import Card from "@/components/Card";
import PageHeader from "@/components/PageHeader";

const STORAGE_KEY = "moatcheck_admin_key";

export default function AdminPage() {
  const [adminKeyInput, setAdminKeyInput] = useState("");
  const [savedKey, setSavedKey] = useState<string | null>(null);

  useEffect(() => {
    setSavedKey(localStorage.getItem(STORAGE_KEY));
  }, []);

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    const k = adminKeyInput.trim();
    if (!k) return;
    localStorage.setItem(STORAGE_KEY, k);
    setSavedKey(k);
    setAdminKeyInput("");
  };

  const forget = () => {
    localStorage.removeItem(STORAGE_KEY);
    setSavedKey(null);
  };

  return (
    <div className="max-w-lg">
      <PageHeader
        title="Admin"
        subtitle="Page reserved for adding new tickers to track. The key entered below is only sent to this tool's server and stays stored only in this browser, never shared anywhere else. This isn't a real authentication system, just a barrier to keep a visitor of the public site from stumbling onto it by accident."
      />

      <Card className="mb-6">
        {savedKey ? (
          <div className="flex items-center justify-between">
            <p className="text-sm text-emerald-700 dark:text-emerald-400 inline-flex items-center gap-1.5">
              <Check size={15} />
              Admin key saved in this browser.
            </p>
            <button
              onClick={forget}
              className="text-sm text-slate-500 dark:text-slate-400 hover:text-red-600 dark:hover:text-rose-400 underline"
            >
              Forget the key
            </button>
          </div>
        ) : (
          <form onSubmit={save} className="flex gap-3 items-end">
            <label className="text-sm flex-1">
              <span className="block text-slate-500 dark:text-slate-400 mb-1 inline-flex items-center gap-1.5">
                <KeyRound size={13} />
                Admin key
              </span>
              <input
                type="password"
                className="border border-slate-200 dark:border-slate-800 rounded-lg px-2 py-1.5 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 w-full text-sm focus:outline-none focus:ring-2 focus:ring-sky-500/30 focus:border-sky-400 dark:focus:border-sky-500 transition-colors"
                value={adminKeyInput}
                onChange={(e) => setAdminKeyInput(e.target.value)}
                placeholder="Paste the ADMIN_API_KEY"
              />
            </label>
            <button
              type="submit"
              className="bg-sky-600 hover:bg-sky-700 text-white px-4 py-1.5 rounded-lg text-sm font-medium transition-colors"
            >
              Save
            </button>
          </form>
        )}
      </Card>

      {savedKey ? (
        <AddStockForm adminKey={savedKey} />
      ) : (
        <p className="text-sm text-slate-400 dark:text-slate-600">Enter the key above to add a ticker.</p>
      )}
    </div>
  );
}
