import { ExternalLink } from 'lucide-react';

export const REPO_URL = 'https://github.com/Shashank-Sund/edip-handoff-app';

/** Shown on every page of the static demo, in the page flow above the top bar, so it never covers a control. */
export function DemoBanner() {
  return (
    <aside aria-label="About this demo" className="border-b bg-muted px-4 py-1.5 text-xs text-muted-foreground sm:px-6">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
        <span>Demo with synthetic data. Runs in your browser; changes reset on reload.</span>
        <a
          href={REPO_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 rounded-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          Source on GitHub
          <ExternalLink className="size-3" aria-hidden />
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      </p>
    </aside>
  );
}
