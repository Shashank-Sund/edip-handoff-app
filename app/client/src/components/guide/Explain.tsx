import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { CircleHelp } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import { GuideSheet } from './HelpButton';

/**
 * A short explanation of a label, in place. It opens on hover and on keyboard focus, and a click or tap keeps it
 * open (touch screens have no hover). With a guide topic it ends in "More in the guide", which opens that topic in
 * the side panel.
 *
 * By default the label itself is the trigger, with a dotted underline. With `icon`, a small ? next to a title or a
 * column header is the trigger and `label` names it.
 */
export function Explain({ text, topic, children, icon, label, className, side = 'top' }: {
  text: ReactNode;
  topic?: string;
  children?: ReactNode;
  icon?: boolean;
  label?: string;
  className?: string;
  side?: 'top' | 'bottom' | 'left' | 'right';
}) {
  const [open, setOpen] = useState(false);
  const [guide, setGuide] = useState(false);
  const pinned = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const more = useRef<HTMLButtonElement>(null);
  const focusMore = useRef(false);
  const inside = useRef(false);
  const quiet = useRef(false);
  const id = useId();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (open && focusMore.current) {
      focusMore.current = false;
      window.requestAnimationFrame(() => more.current?.focus());
    }
  }, [open]);
  const show = () => {
    window.clearTimeout(timer.current);
    setOpen(true);
  };
  const hide = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => !pinned.current && setOpen(false), 150);
  };
  return (
    <>
      <Popover
        open={open}
        onOpenChange={(o) => {
          if (!o) pinned.current = false;
          setOpen(o);
        }}
      >
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={icon ? `About ${label}` : undefined}
            aria-describedby={id}
            onPointerEnter={(e) => e.pointerType === 'mouse' && show()}
            onPointerLeave={(e) => e.pointerType === 'mouse' && hide()}
            onFocus={() => {
              if (quiet.current) quiet.current = false;
              else show();
            }}
            onBlur={hide}
            onClick={(e) => {
              // Rows and cards around a label open the patient on click; this click is only for the explanation.
              e.preventDefault();
              e.stopPropagation();
              if (open && pinned.current) {
                pinned.current = false;
                setOpen(false);
                return;
              }
              pinned.current = true;
              focusMore.current = e.detail === 0 && Boolean(topic); // Enter or Space: move to the guide link
              if (open && focusMore.current) {
                focusMore.current = false;
                more.current?.focus();
              }
              setOpen(true);
            }}
            className={cn(
              icon
                ? 'inline-grid size-5 shrink-0 place-items-center rounded-full align-middle text-muted-foreground/80 transition-colors hover:text-foreground'
                : 'cursor-help rounded-sm text-left underline decoration-muted-foreground/60 decoration-dotted underline-offset-[3px]',
              'outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
              className,
            )}
          >
            {icon ? <CircleHelp className="size-3.5" aria-hidden /> : children}
          </button>
        </PopoverTrigger>
        <PopoverContent
          side={side}
          aria-label={label ?? (typeof children === 'string' ? children : 'Explanation')}
          className="w-72 max-w-[calc(100vw-2rem)] p-3 text-xs font-normal normal-case leading-snug tracking-normal text-popover-foreground"
          onOpenAutoFocus={(e) => e.preventDefault()}
          onCloseAutoFocus={(e) => {
            // Back to the label only when focus was in the panel (Escape from the guide link), never after a hover.
            if (!inside.current) e.preventDefault();
            else quiet.current = true;
            inside.current = false;
          }}
          onPointerEnter={show}
          onPointerLeave={(e) => e.pointerType === 'mouse' && hide()}
          onFocus={() => {
            inside.current = true;
            show();
          }}
          onBlur={hide}
        >
          <div className="text-pretty">{text}</div>
          {topic && (
            <button
              ref={more}
              type="button"
              className="mt-2 rounded-sm font-medium text-primary outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/60"
              onClick={() => {
                pinned.current = false;
                setOpen(false);
                setGuide(true);
              }}
            >
              More in the guide
            </button>
          )}
        </PopoverContent>
      </Popover>
      <span id={id} className="sr-only">
        {text}
      </span>
      {topic && <GuideSheet topic={topic} open={guide} onOpenChange={setGuide} />}
    </>
  );
}
