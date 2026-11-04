import { useState } from 'react';
import { CircleHelp } from 'lucide-react';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { WithTooltip } from '@/components/ui/tooltip';
import { linkProps } from '@/lib/router';
import { cn } from '@/lib/utils';
import { anchorFor, findTopic } from './content';

/** One topic (or one whole section) of the guide in a side panel, with a link to the full guide. */
export function GuideSheet({ topic, open, onOpenChange }: { topic: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const found = findTopic(topic);
  if (!found) return null;
  const { section, topics } = found;
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {open && (
        <SheetContent title={topics.length === 1 ? topics[0].title : section.title} description={topics.length === 1 ? section.title : section.summary}>
          {/* Any link inside (to the guide, or within it) leaves the page, so the panel closes. */}
          <div className="space-y-6 px-5 py-5 text-sm leading-relaxed" onClickCapture={(e) => (e.target as HTMLElement).closest('a') && onOpenChange(false)}>
            {topics.map((t) => (
              <section key={t.id} className="space-y-3">
                {topics.length > 1 && <h3 className="font-semibold">{t.title}</h3>}
                {t.body}
              </section>
            ))}
            <a {...linkProps(`/guide#${topics.length === 1 ? anchorFor(`${section.id}.${topics[0].id}`) : section.id}`)} className="inline-block text-sm font-medium text-primary hover:underline">
              Open the full guide
            </a>
          </div>
        </SheetContent>
      )}
    </Sheet>
  );
}

/** A ? button that opens one topic of the guide in a side panel, without leaving the page. */
export function HelpButton({ topic, label = 'How to read this', className }: { topic: string; label?: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const found = findTopic(topic);
  if (!found) return null;
  const { section, topics } = found;
  return (
    <>
      <WithTooltip label={label}>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setOpen(true);
          }}
          className={cn('inline-grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50', className)}
          aria-label={`${label}: ${topics.length === 1 ? topics[0].title : section.title}`}
        >
          <CircleHelp className="size-4" aria-hidden />
        </button>
      </WithTooltip>
      <GuideSheet topic={topic} open={open} onOpenChange={setOpen} />
    </>
  );
}
