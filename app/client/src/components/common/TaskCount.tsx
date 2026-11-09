import { ListTodo } from 'lucide-react';
import { Explain } from '@/components/guide/Explain';
import { taskCountLabel } from '@/lib/tasks';
import { cn } from '@/lib/utils';
import type { TaskCounts } from '@/lib/types';

/** The open and overdue tasks of a board row; nothing when none is open. plain: no explanation (inside a link). */
export function TaskCount({ counts, plain }: { counts?: TaskCounts; plain?: boolean }) {
  if (!counts?.open) return null;
  const chip = (
    <span className={cn('inline-flex items-center gap-1 whitespace-nowrap text-xs', counts.overdue ? 'font-medium text-sev-unstable' : 'text-muted-foreground')}>
      <ListTodo className="size-3.5" aria-hidden />
      {taskCountLabel(counts)}
    </span>
  );
  if (plain) return chip;
  return (
    // The row opens the handoff on click; the explanation must not.
    <span onClick={(e) => e.stopPropagation()}>
      <Explain
        topic="tasks.board"
        text="Tasks not done yet for this patient, from signed action lists and added by hand, and how many of them are past their due time. Open the patient for the list."
      >
        {chip}
      </Explain>
    </span>
  );
}
