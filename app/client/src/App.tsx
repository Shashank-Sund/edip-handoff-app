import { lazy, Suspense } from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { ErrorState, PageSkeleton } from '@/components/common/states';
import { AppProvider, useConfig } from '@/hooks/useApp';
import { match, usePath } from '@/lib/router';
import Board from '@/pages/Board';

const Handoff = lazy(() => import('@/pages/Handoff'));
const Analytics = lazy(() => import('@/pages/Analytics'));
const Templates = lazy(() => import('@/pages/Templates'));
const About = lazy(() => import('@/pages/About'));

function Routes() {
  const path = usePath();
  const h = match(path, '/handoff/:csn');
  if (h) return <Handoff key={h.csn} csn={h.csn} />;
  if (path.startsWith('/analytics')) return <Analytics />;
  if (path.startsWith('/templates')) return <Templates />;
  if (path.startsWith('/about')) return <About />;
  return <Board />;
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
