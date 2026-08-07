"use client";

import { Megaphone, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

type AnnouncementText = {
  title: string;
  body: string;
};

type StaticUpdateAnnouncement = {
  id: string;
  publishedAt: string;
  expiresAt: string;
  zh: AnnouncementText;
  en: AnnouncementText;
};

type UpdateAnnouncementButtonProps = {
  isEn: boolean;
};

const STATIC_UPDATE_ANNOUNCEMENTS: StaticUpdateAnnouncement[] = [
  {
    id: "feedback-status-2026-06",
    publishedAt: "2026-06-07T00:00:00+08:00",
    expiresAt: "2026-07-15T00:00:00+08:00",
    zh: {
      title: "Update: announcements and feedback status are now live",
      body:
        "PolyWeather Terminal now has an update announcement entry. Future product updates, data-source changes, and important notes will be shared here.\n\n" +
        "The feedback system has also been upgraded. When users submit feedback, PolyWeather automatically attaches the relevant chart context. Users can now track their own feedback status from the terminal notification entry and the account page: received, confirmed, in progress, resolved, or closed.\n\n" +
        "We are also considering small rewards such as points or Pro days for real, reproducible, and constructive feedback.",
    },
    en: {
      title: "Update: announcements and feedback status are now live",
      body:
        "PolyWeather Terminal now has an update announcement entry. Future product updates, data-source changes, and important notes will be shared here.\n\n" +
        "The feedback system has also been upgraded. When users submit feedback, PolyWeather automatically attaches the relevant chart context. Users can now track their own feedback status from the terminal notification entry and the account page: received, confirmed, in progress, resolved, or closed.\n\n" +
        "We are also considering small rewards such as points or Pro days for real, reproducible, and constructive feedback.",
    },
  },
];

function isActiveAnnouncement(item: StaticUpdateAnnouncement, now = Date.now()) {
  const expiresAt = Date.parse(item.expiresAt);
  if (!Number.isFinite(expiresAt) || expiresAt <= now) return false;
  const publishedAt = Date.parse(item.publishedAt);
  return !Number.isFinite(publishedAt) || publishedAt <= now;
}

function pickAnnouncementText(payload: StaticUpdateAnnouncement, isEn: boolean) {
  const primary = payload.en;
  return {
    title: String(primary?.title || "").trim(),
    body: String(primary?.body || "").trim(),
  };
}

function formatUpdatedAt(value: string | undefined, isEn: boolean) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return date.toLocaleString(isEn ? "en-US" : "zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function UpdateAnnouncementButton({ isEn }: UpdateAnnouncementButtonProps) {
  const [open, setOpen] = useState(false);
  const shellRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!shellRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [open]);

  const announcement = useMemo(
    () => STATIC_UPDATE_ANNOUNCEMENTS.find((item) => isActiveAnnouncement(item)) ?? null,
    [],
  );
  const text = useMemo(
    () => (announcement ? pickAnnouncementText(announcement, isEn) : { title: "", body: "" }),
    [announcement, isEn],
  );
  const updatedAt = useMemo(
    () => formatUpdatedAt(announcement?.publishedAt, isEn),
    [announcement?.publishedAt, isEn],
  );

  if (!announcement || (!text.title && !text.body)) return null;

  return (
    <div ref={shellRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex h-7 items-center gap-1.5 rounded border border-blue-200 bg-blue-50 px-2 text-[10px] font-bold uppercase tracking-wide text-blue-700 transition-colors hover:border-blue-300 hover:bg-blue-100"
        title={isEn ? "Update announcement" : "Update announcement"}
        aria-expanded={open}
      >
        <Megaphone size={12} />
        {isEn ? "Updates" : "Updates"}
      </button>
      {open && (
        <div className="absolute left-0 top-8 z-50 w-[min(360px,calc(100vw-32px))] rounded-md border border-slate-200 bg-white p-3 text-left shadow-lg">
          <div className="flex items-start gap-3">
            <div className="grid h-7 w-7 shrink-0 place-items-center rounded border border-blue-100 bg-blue-50 text-blue-600">
              <Megaphone size={14} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs font-bold leading-5 text-slate-900">
                {text.title || (isEn ? "PolyWeather update" : "PolyWeather update")}
              </div>
              {updatedAt && (
                <div className="mt-0.5 font-mono text-[10px] text-slate-400">
                  {isEn ? "Updated" : "Updated"} {updatedAt}
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="grid h-6 w-6 shrink-0 place-items-center rounded border border-slate-200 text-slate-400 hover:bg-slate-50 hover:text-slate-700"
              title={isEn ? "Close" : "Close"}
            >
              <X size={12} />
            </button>
          </div>
          {text.body && (
            <p className="mt-3 whitespace-pre-line text-[12px] font-medium leading-5 text-slate-600">
              {text.body}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
