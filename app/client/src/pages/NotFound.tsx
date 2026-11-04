import { Compass } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Layout } from '@/components/layout/Layout';
import { linkProps } from '@/lib/router';

/** Any address the app does not know: say so, and offer the way back. */
export default function NotFound() {
  return (
    <Layout title="Page not found">
      <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-10 text-center">
        <Compass className="size-6 text-muted-foreground" aria-hidden />
        <h2 className="text-base font-semibold">This page does not exist</h2>
        <p className="max-w-md text-sm text-muted-foreground">The link may be old or mistyped. The board lists every patient with a handoff.</p>
        <Button asChild>
          <a {...linkProps('/')}>Go to the board</a>
        </Button>
      </div>
    </Layout>
  );
}
