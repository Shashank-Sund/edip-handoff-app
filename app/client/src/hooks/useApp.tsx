import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, setPersona as setApiPersona } from '@/lib/api';
import { setTimeZone } from '@/lib/format';
import { readString, writeString } from '@/lib/storage';
import type { AppConfig, Persona } from '@/lib/types';

interface AppState {
  config: AppConfig;
  persona: Persona;
  setPersona: (id: string) => void;
  /** The app clock: the replay time in mock mode, the current time live. */
  at: string;
  setAt: (iso: string) => void;
  playing: boolean;
  setPlaying: (v: boolean) => void;
  /** Bumped after every action so views refetch. */
  version: number;
  bump: () => void;
}

const Ctx = createContext<AppState | null>(null);
const PERSONA_KEY = 'edip.persona';
const STEP_MS = 1500; // one replay step (15 minutes of the week) every 1.5 seconds
const STEP_MIN = 15;

/** The replay time in the address (?at=), so a reload or a shared link opens at the same moment. */
function urlAt(): string | null {
  const v = new URLSearchParams(window.location.search).get('at');
  return v && !Number.isNaN(new Date(v).getTime()) ? v : null;
}

function writeUrlAt(iso: string | null) {
  const url = new URL(window.location.href);
  if (iso) url.searchParams.set('at', iso);
  else url.searchParams.delete('at');
  if (url.href !== window.location.href) window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
}

export function AppProvider({ config, children }: { config: AppConfig; children: ReactNode }) {
  const initial = readString(PERSONA_KEY);
  const [personaId, setPersonaId] = useState(() =>
    config.personas.some((p) => p.id === initial) ? (initial as string) : config.personas[0].id,
  );
  const clampTo = (iso: string) => {
    const t = Math.min(Math.max(new Date(iso).getTime(), new Date(config.window.start).getTime()), new Date(config.window.end).getTime());
    return new Date(t).toISOString();
  };
  const [at, setAtState] = useState(() => {
    if (config.clock === 'live') return new Date().toISOString();
    const fromUrl = urlAt();
    return fromUrl ? clampTo(fromUrl) : config.default_at;
  });
  const [playing, setPlaying] = useState(false);
  const [version, setVersion] = useState(0);
  const persona = config.personas.find((p) => p.id === personaId) ?? config.personas[0];
  setApiPersona(persona.id);

  const setPersona = useCallback((id: string) => {
    setApiPersona(id);
    writeString(PERSONA_KEY, id);
    setPersonaId(id);
  }, []);

  const clamp = useCallback(
    (iso: string) => {
      const t = Math.min(Math.max(new Date(iso).getTime(), new Date(config.window.start).getTime()), new Date(config.window.end).getTime());
      return new Date(t).toISOString();
    },
    [config.window],
  );
  const setAt = useCallback((iso: string) => setAtState(clamp(iso)), [clamp]);

  // Keep ?at= in the address in step with the replay clock (and after in app navigation, which drops it).
  useEffect(() => {
    if (config.clock === 'live') return;
    writeUrlAt(at);
    const on = () => writeUrlAt(at);
    window.addEventListener('popstate', on);
    return () => window.removeEventListener('popstate', on);
  }, [at, config.clock]);

  // Replay: advance the clock while playing; live: follow the wall clock.
  useEffect(() => {
    if (config.clock === 'live') {
      const id = window.setInterval(() => setAtState(new Date().toISOString()), 20000);
      return () => window.clearInterval(id);
    }
    if (!playing) return;
    const id = window.setInterval(() => {
      setAtState((cur) => {
        const next = clamp(new Date(new Date(cur).getTime() + STEP_MIN * 60000).toISOString());
        if (next === cur) setPlaying(false);
        return next;
      });
    }, STEP_MS);
    return () => window.clearInterval(id);
  }, [playing, config.clock, clamp]);

  const bump = useCallback(() => setVersion((v) => v + 1), []);
  const value = useMemo(
    () => ({ config, persona, setPersona, at, setAt, playing, setPlaying, version, bump }),
    [config, persona, setPersona, at, setAt, playing, version, bump],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useApp outside AppProvider');
  return v;
}

/** Load the app config once. */
export function useConfig() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let stop = false;
    let timer = 0;
    const load = () =>
      api
        .config()
        .then(async (c) => (c.personas.length ? c : { ...c, personas: [await api.me()] }))
        .then(
          (c) => {
            if (stop) return;
            setTimeZone(c.timezone);
            setError(null);
            setConfig(c);
            // Live mode before setup finishes: check again every 30 seconds.
            if (c.ready === false) timer = window.setTimeout(load, 30000);
          },
          (e: Error) => !stop && setError(e.message),
        );
    load();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, []);
  return { config, error };
}
