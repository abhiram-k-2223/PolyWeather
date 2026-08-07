"use client";

import { createContext, useContext, useMemo } from "react";
import {
  formatMessage,
} from "@/lib/i18n";

interface I18nContextValue {
  locale: "en-US";
  t: (key: string, params?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const value = useMemo<I18nContextValue>(
    () => ({
      locale: "en-US",
      t: (key, params) => formatMessage(key, params),
    }),
    [],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return context;
}
