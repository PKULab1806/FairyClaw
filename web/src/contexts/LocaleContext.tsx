import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import en from '../locales/en.json'
import zh from '../locales/zh.json'

const LOCALE_KEY = 'fairyclaw-web-locale'

export type Locale = 'zh' | 'en'

const dictionaries: Record<Locale, Record<string, string>> = { zh, en }

function interpolate(template: string, vars?: Record<string, string>): string {
  if (!vars) {
    return template
  }
  let s = template
  for (const [k, v] of Object.entries(vars)) {
    s = s.replaceAll(`{${k}}`, v)
  }
  return s
}

type LocaleContextValue = {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: (key: string, vars?: Record<string, string>) => string
}

const LocaleContext = createContext<LocaleContextValue | null>(null)

function readInitialLocale(): Locale {
  try {
    const raw = localStorage.getItem(LOCALE_KEY)
    if (raw === 'en' || raw === 'zh') {
      return raw
    }
  } catch {
    // ignore
  }
  return 'zh'
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readInitialLocale)

  useEffect(() => {
    document.documentElement.lang = locale === 'zh' ? 'zh-CN' : 'en'
    try {
      localStorage.setItem(LOCALE_KEY, locale)
    } catch {
      // ignore
    }
  }, [locale])

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next)
  }, [])

  const t = useCallback(
    (key: string, vars?: Record<string, string>) => {
      const dict = dictionaries[locale]
      const template = dict[key] ?? dictionaries.en[key] ?? key
      return interpolate(template, vars)
    },
    [locale],
  )

  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t])

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
}

export function useLocale(): LocaleContextValue {
  const ctx = useContext(LocaleContext)
  if (!ctx) {
    throw new Error('useLocale must be used within LocaleProvider')
  }
  return ctx
}
