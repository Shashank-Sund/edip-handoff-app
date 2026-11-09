import { ListTodo } from 'lucide-react';
import { Explain } from '@/components/guide/Explain';
import { cn } from '@/lib/utils';
import type { TaskCounts } from '@/lib/types';

const words = (c: TaskCounts) => `${c.open} task${c.open === 1 ? '' : 's'} open${c.overdue ? `, ${c.overdue} overdue` : ''}`;

/** The open and overdue tasks of a board row; nothing when none is open. plain: no explanation (inside a link). */
export function TaskCount({ counts, plain }: { counts?: TaskCounts; plain?: boolean }) {
  if (!counts?.open) return null;
  const chip = (
    <span className={cn('inline-flex items-center gap-1 whitespace-nowrap text-xs', counts.overdue ? 'font-medium text-sev-unstable' : 'text-muted-foreground')}>
      <ListTodo className="size-3.5" aria-hidden />
      {counts.open} open{counts.overdue > 0 && ` · ${counts.overdue} overdue`}
    </span>
  );
  if (plain) return <span aria-label={words(counts)}>{chip}</span>;
  return (
    // The row opens the handoff on click; the explanation must not.
    <span onClick={(e) => e.stopPropagation()}>
      <Explain
        topic="tasks.board"
        label={words(counts)}
        text="Open tasks for this patient, from signed action lists and added by hand. Overdue: open and past its due time. Open the patient for the list."
      >
        {chip}
      </Explain>
    </span>
  );
}
