"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  User as UserIcon,
  ShieldCheck,
  Fingerprint,
  Bot,
  RefreshCw,
  LogOut,
  ChevronLeft,
  Mail,
  LogIn,
  Clock,
  ExternalLink,
  Loader2,
} from "lucide-react";
import {
  getSupabaseBrowserClient,
  hasSupabasePublicEnv,
} from "@/lib/supabase/client";
import { useI18n } from "@/hooks/useI18n";

import {
  TELEGRAM_BOT_URL,
  TELEGRAM_GROUP_URL,
  TELEGRAM_TOPICS_GROUP_URL,
} from "./constants";
import { InfoRow } from "./AccountInfoRow";
import { AccountFeedbackPanel } from "./AccountFeedbackPanel";
import { createAccountCopy } from "./account-copy";

// --- Main Component ---

type AccountIdentity = {
  authenticated: boolean;
  user_id?: string | null;
  email?: string | null;
};

export function AccountCenter() {
  const router = useRouter();
  const { locale } = useI18n();
  const isEn = locale === "en-US";
  const copy = useMemo(() => createAccountCopy(isEn), [isEn]);

  // ── UI-only state ──────────────────────────────────────
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [identity, setIdentity] = useState<AccountIdentity | null>(null);
  const [joinedAt, setJoinedAt] = useState("");

  const supabaseReady = hasSupabasePublicEnv();

  // ── Load identity from backend + Supabase ──────────────
  const loadIdentity = useCallback(async () => {
    if (typeof fetch !== "function") return;
    try {
      await fetch("/api/auth/me", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      })
        .then((res) => (res.ok ? res.json() : null))
        .then((payload) => {
          const authMe = (payload || {}) as AccountIdentity;
          setIdentity(authMe);
        })
        .catch(() => {
          setIdentity({ authenticated: false });
        });
      if (supabaseReady) {
        try {
          const {
            data: { session },
          } = await getSupabaseBrowserClient().auth.getSession();
          const email = String(session?.user?.email || "").trim();
          const createdAt = String(session?.user?.created_at || "").trim();
          if (email) {
            setJoinedAt(createdAt);
            setIdentity((prev) => ({
              authenticated: true,
              user_id: prev?.user_id || session?.user?.id || null,
              email: prev?.email || email,
            }));
          }
        } catch {
          // ignore supabase errors
        }
      }
    } finally {
      setLoading(false);
    }
  }, [supabaseReady]);

  // ── Refresh ────────────────────────────────────────────
  const onRefresh = async () => {
    setRefreshing(true);
    await loadIdentity();
    setRefreshing(false);
  };

  // ── Sign out ────────────────────────────────────────────
  const onSignOut = async () => {
    if (supabaseReady) {
      try {
        await getSupabaseBrowserClient().auth.signOut();
      } catch {
        // ignore
      }
    }
    setIdentity(null);
    router.replace("/");
  };

  // ── Derived display values ──────────────────────────────
  const isAuthenticated = Boolean(identity?.authenticated && identity?.user_id);
  const email = String(identity?.email || "").trim();
  const userId = String(identity?.user_id || "");
  const displayName =
    (email ? String(email).split("@")[0] : "") || copy.guestUser;
  const initials = (displayName.slice(0, 2) || "PW").toUpperCase();
  const joinedLabel = formatDateLabel(joinedAt, locale);

  if (loading && !refreshing) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-[#f4f7fb]">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-12 w-12 animate-spin text-blue-500" />
          <p className="font-medium text-slate-500">{copy.loadingAccount}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex min-h-screen w-full flex-col items-center overflow-hidden bg-[#f4f7fb] p-4 font-sans text-slate-900 md:p-8">
      <div className="absolute inset-x-0 top-0 h-20 border-b border-slate-200 bg-white"></div>

      {/* Header */}
      <header className="w-full max-w-6xl flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8 z-20">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="group rounded-lg border border-slate-200 bg-white p-2 text-slate-500 shadow-sm transition-all hover:border-slate-300 hover:text-slate-950 active:scale-95"
            title={copy.backHome}
            aria-label={copy.backHome}
          >
            <ChevronLeft
              size={20}
              className="group-hover:-translate-x-0.5 transition-transform"
            />
          </Link>
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold text-slate-950">
              {copy.accountCenter}
            </h1>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void onRefresh()}
            className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 shadow-sm transition-all hover:border-slate-300 hover:text-slate-950 disabled:opacity-50"
            disabled={refreshing}
          >
            <RefreshCw size={16} className={refreshing ? "animate-spin" : ""} />
            {"Refresh"}
          </button>
          {isAuthenticated ? (
            <button
              onClick={() => void onSignOut()}
              className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm font-semibold text-red-700 transition-all hover:bg-red-100"
            >
              <LogOut size={16} /> {copy.signOut}
            </button>
          ) : (
            <Link
              href="/auth/login?next=%2Faccount"
              className="flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-semibold text-blue-700 transition-all hover:bg-blue-100"
            >
              <LogIn size={16} /> {copy.signIn}
            </Link>
          )}
        </div>
      </header>

      <main className="w-full max-w-6xl grid grid-cols-1 lg:grid-cols-12 gap-6 z-10 relative">
        {/* User Card */}
        <div className="flex flex-col items-center gap-8 rounded-2xl border border-slate-200 bg-white p-8 shadow-sm lg:col-span-8 md:flex-row">
          <div className="relative">
            <div className="flex h-24 w-24 items-center justify-center rounded-xl border border-blue-200 bg-blue-600 text-3xl font-bold text-white shadow-sm">
              {initials}
            </div>
            <div className="absolute -bottom-2 -right-2 rounded-lg border-4 border-white bg-slate-100 p-1.5 text-slate-400">
              <Fingerprint size={16} />
            </div>
          </div>
          <div className="flex flex-1 flex-col items-center gap-2 text-center md:items-start md:text-left">
            <div>
              <h2 className="flex items-center justify-center md:justify-start gap-3 text-3xl font-bold text-slate-950">
                {displayName}
              </h2>
            </div>
            <p className="mb-1 font-mono text-sm text-slate-500">
              {email || copy.guestUser}
            </p>
            <div className="flex flex-wrap justify-center md:justify-start gap-4">
              <div className="flex items-center gap-1.5 text-xs text-slate-500">
                <UserIcon size={14} />{" "}
                <span className="font-mono">
                  {userId ? `${userId.substring(0, 12)}...` : "--"}
                </span>
              </div>
              {joinedLabel && (
                <div className="flex items-center gap-1.5 text-xs text-slate-500">
                  <Clock size={14} />{" "}
                  <span>
                    {copy.joinedAt}: {joinedLabel}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Telegram links */}
        <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm lg:col-span-4">
          <h3 className="mb-4 flex items-center gap-2 text-sm font-bold uppercase text-blue-700">
            <Bot size={18} /> {copy.telegramHeading}
          </h3>
          <div className="flex flex-col gap-3">
            {TELEGRAM_BOT_URL ? (
              <a
                href={TELEGRAM_BOT_URL}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-semibold text-slate-700 transition-all hover:border-blue-300 hover:bg-blue-50"
              >
                <span>{copy.telegramBotLink}</span>
                <ExternalLink size={14} />
              </a>
            ) : null}
            {TELEGRAM_GROUP_URL ? (
              <a
                href={TELEGRAM_GROUP_URL}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-semibold text-slate-700 transition-all hover:border-blue-300 hover:bg-blue-50"
              >
                <span>{copy.telegramGroupLink}</span>
                <ExternalLink size={14} />
              </a>
            ) : null}
            {TELEGRAM_TOPICS_GROUP_URL &&
            TELEGRAM_TOPICS_GROUP_URL !== TELEGRAM_GROUP_URL ? (
              <a
                href={TELEGRAM_TOPICS_GROUP_URL}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm font-semibold text-slate-700 transition-all hover:border-emerald-300 hover:bg-emerald-50"
              >
                <span>{copy.telegramTopicsGroupLink}</span>
                <ExternalLink size={14} />
              </a>
            ) : null}
          </div>
        </section>

        {/* Identity Status */}
        <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm lg:col-span-12">
          <h3 className="mb-4 text-sm font-bold uppercase text-indigo-700">
            {copy.identityStatus}
          </h3>
          <InfoRow icon={ShieldCheck} label={copy.authMode} value="Supabase" />
          <InfoRow icon={Mail} label={copy.boundEmail} value={email || "--"} />
          <InfoRow
            icon={LogIn}
            label={copy.loginMethod}
            value={isAuthenticated ? "Supabase" : copy.guestUser}
          />
        </section>

        <AccountFeedbackPanel
          title={copy.accountFeedbackTitle}
          description={copy.accountFeedbackDescription}
          refreshLabel="Refresh"
        />
      </main>

      <footer className="mt-16 text-center text-slate-600 text-[10px] uppercase tracking-[0.3em] font-mono z-10 pb-8">
        {copy.footerEngine}
      </footer>
    </div>
  );
}

function formatDateLabel(raw: string, locale: string): string {
  if (!raw) return "";
  try {
    const dt = new Date(raw);
    if (Number.isNaN(dt.getTime())) return "";
    return new Intl.DateTimeFormat(locale, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(dt);
  } catch {
    return "";
  }
}