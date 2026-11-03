import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, setPersona as setApiPersona } from '@/lib/api';
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

export function AppProvider({ config, children }: { config: AppConfig; children: ReactNode }) {
  const initial = readString(PERSONA_KEY);
  const [personaId, setPersonaId] = useState(() =>
    config.personas.some((p) => p.id === initial) ? (initial as string) : config.personas[0].id,
  );
  const [at, setAtState] = useState(config.clock === 'live' ? new Date().toISOString() : config.default_at);
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
    api
      .config()
      .then(async (c) => (c.personas.length ? c : { ...c, personas: [await api.me()] }))
      .then(setConfig, (e: Error) => setError(e.message));
  }, []);
  return { config, error };
}
