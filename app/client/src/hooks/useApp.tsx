import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, setPersona as setApiPersona } from '@/lib/api';
import { setTimeZone } from '@/lib/format';
import { readString, writeString } from '@/lib/storage';
import type { AppConfig, Persona } from '@/lib/types';

interface AppState {
  config: AppConfig;
  persona: Persona;
  setPersona: (id: string) => void;
  /** The app clock: the replay time in mock mode; live, the current time or the earlier time being viewed. */
  at: string;
  setAt: (iso: string) => void;
  /** The `at` to send to the server: `at`, except live at the current time, when the server's own clock decides (''). */
  apiAt: string;
  /** Live clock: the earlier time being viewed (read only), or null for live. */
  past: string | null;
  setPast: (iso: string | null) => void;
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
/** Live: an earlier time this close to now is now (the server says the same), as device clocks differ a little. */
export const NOW_SLACK_MS = 2 * 60000;

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
  const live = config.clock === 'live';
  const pastHours = live ? config.past_view_hours ?? 0 : 0;
  // Live: an earlier time from the address (?at=) when looking back is on; a later one is now. The server refuses
  // a time older than its limit and says so.
  const [past, setPastState] = useState<string | null>(() => {
    const fromUrl = pastHours > 0 ? urlAt() : null;
    return fromUrl && new Date(fromUrl).getTime() < Date.now() - NOW_SLACK_MS ? new Date(fromUrl).toISOString() : null;
  });
  const [at, setAtState] = useState(() => {
    if (live) return new Date().toISOString();
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
  /** Live: look at an earlier time; null (or a time within the slack of now, or later) is back to live. */
  const setPast = useCallback((iso: string | null) => {
    const t = iso ? new Date(iso).getTime() : NaN;
    setPastState(t < Date.now() - NOW_SLACK_MS ? new Date(t).toISOString() : null);
    setAtState(new Date().toISOString());
  }, []);

  // Keep ?at= in the address in step with the replay clock, or the earlier time viewed live (and after in app
  // navigation, which drops it).
  const inUrl = live ? past : at;
  useEffect(() => {
    writeUrlAt(inUrl);
    const on = () => writeUrlAt(inUrl);
    window.addEventListener('popstate', on);
    return () => window.removeEventListener('popstate', on);
  }, [inUrl]);

  // Replay: advance the clock while playing; live: follow the wall clock, paused while viewing an earlier time.
  useEffect(() => {
    if (live) {
      if (past) return;
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
  }, [playing, live, past, clamp]);

  const bump = useCallback(() => setVersion((v) => v + 1), []);
  const value = useMemo(
    () => ({
      config, persona, setPersona, at: live ? past ?? at : at, setAt, apiAt: live ? past ?? '' : at, past: live ? past : null,
      setPast, playing, setPlaying, version, bump,
    }),
    [config, persona, setPersona, live, past, at, setAt, setPast, playing, version, bump],
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
