import { lazy, Suspense } from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { ErrorState, PageSkeleton } from '@/components/common/states';
import { AppProvider, useConfig } from '@/hooks/useApp';
import { match, usePath } from '@/lib/router';
import Board from '@/pages/Board';
import NotFound from '@/pages/NotFound';

const Handoff = lazy(() => import('@/pages/Handoff'));
const Analytics = lazy(() => import('@/pages/Analytics'));
const Templates = lazy(() => import('@/pages/Templates'));
const Guide = lazy(() => import('@/pages/Guide'));
const Takeover = lazy(() => import('@/pages/Takeover'));
const Tasks = lazy(() => import('@/pages/Tasks'));

function Routes() {
  const path = usePath();
  const h = match(path, '/handoff/:csn');
  if (h) return <Handoff key={h.csn} csn={h.csn} />;
  if (match(path, '/analytics')) return <Analytics />;
  if (match(path, '/templates')) return <Templates />;
  if (match(path, '/takeover')) return <Takeover />;
  if (match(path, '/tasks')) return <Tasks />;
  if (match(path, '/guide') || match(path, '/about')) return <Guide />;
  if (path === '/' || path === '' || path === '/index.html') return <Board />;
  return <NotFound />;
}

/** Live mode before the setup job has loaded the data: say so; useConfig checks again every 30 seconds. */
function NotReady({ detail }: { detail?: string | null }) {
  return (
    <div className="mx-auto max-w-lg p-6 pt-24">
      <ErrorState message={`${detail ?? 'The handoff data is not loaded yet.'} This page checks again every 30 seconds.`} onRetry={() => window.location.reload()} />
    </div>
  );
}

export default function App() {
  const { config, error } = useConfig();
  if (error) {
    return (
      <div className="mx-auto max-w-lg p-6 pt-24">
        <ErrorState message={error} onRetry={() => window.location.reload()} />
      </div>
    );
  }
  if (!config) {
    return (
      <div className="mx-auto max-w-6xl p-6">
        <PageSkeleton />
      </div>
    );
  }
  if (config.ready === false) return <NotReady detail={config.detail} />;
  return (
    <AppProvider config={config}>
      <TooltipProvider delayDuration={250}>
        <Suspense fallback={<div className="p-6"><PageSkeleton /></div>}>
          <Routes />
        </Suspense>
      </TooltipProvider>
    </AppProvider>
  );
}
