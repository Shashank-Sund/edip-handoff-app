import { useEffect, useRef, useState } from 'react';

/**
 * Fetch with dependencies. Keeps showing the previous data while a refetch runs (no flicker when the clock
 * moves), and drops answers that arrive out of order.
 */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  useEffect(() => {
    const n = ++seq.current;
    setLoading(true);
    fn().then(
      (d) => {
        if (n !== seq.current) return;
        setData(d);
        setError(null);
        setLoading(false);
      },
      (e: Error) => {
        if (n !== seq.current) return;
        setError(e.message);
        setLoading(false);
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, setData };
}
