import { useEffect, useId, useState } from 'react';
import { CalendarClock, Check, ChevronDown, ChevronRight, CircleSlash, RotateCcw, UserRoundPen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input, fieldClasses } from '@/components/ui/input';
import { Pill } from '@/components/common/badges';
import type { TaskChange } from '@/lib/api';
import { clock } from '@/lib/format';
import { TEMPLATE_NAME } from '@/lib/labels';
import { TASK_ROLE, UNDO_LABEL, dueLabel, forWhom, nextAt, origin, stepLabel } from '@/lib/tasks';
import { cn } from '@/lib/utils';
import type { Task, TaskPerson } from '@/lib/types';

/** Saves one change to a task; resolves true when the server took it. ok: what the status line says then. */
export type TaskChangeFn = (task: Task, body: TaskChange, ok: string) => Promise<boolean>;

const MAX_REASON = 200;

/**
 * One task with its actions: Done, Not needed (with a reason), Reassign (to the nurse, the physician or a
 * colleague), Due time, and Undo of the last change. people: who it can go to, or a loader for them (the Tasks page
 * asks the patient's task list only when Reassign opens).
 */
export function TaskItem({ task, at, people, canAct, locked, busy, onChange, extra }: {
  task: Task;
  at: string;
  people: TaskPerson[] | (() => Promise<TaskPerson[]>);
  canAct: boolean;
  locked: boolean;
  busy: boolean;
  onChange: TaskChangeFn;
  /** Shown above the actions: the patient, on the Tasks page. */
  extra?: React.ReactNode;
}) {
  const [mode, setMode] = useState<null | 'not_needed' | 'reassign' | 'due'>(null);
  const [reason, setReason] = useState('');
  const [target, setTarget] = useState('');
  const [time, setTime] = useState('');
  const [list, setList] = useState<TaskPerson[] | null>(Array.isArray(people) ? people : null);
  const [history, setHistory] = useState(false);
  const id = useId();
  useEffect(() => {
    if (Array.isArray(people)) setList(people);
  }, [people]);
  const open = task.status === 'open';
  const off = !canAct || locked || busy;
  const save = async (body: TaskChange, ok: string) => {
    if (await onChange(task, body, ok)) {
      setMode(null);
      setReason('');
      setTarget('');
      setTime('');
    }
  };
  const openReassign = async () => {
    setMode(mode === 'reassign' ? null : 'reassign');
    if (!list && typeof people === 'function') setList(await people());
  };
  const due = time ? nextAt(time, at) : null;
  const targetBody = (): TaskChange => (target === 'nurse' || target === 'physician' ? { kind: 'reassign', role: target } : { kind: 'reassign', to: target });
  const targetName = target === 'nurse' || target === 'physician' ? `the ${target}` : list?.find((p) => p.id === target)?.name ?? '';

  return (
    <li className={cn('space-y-2 px-4 py-3 sm:px-5', !open && 'bg-muted/30')} aria-label={task.text}>
      {extra}
      <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 space-y-1">
          <p className={cn('text-sm font-medium', !open && 'text-muted-foreground line-through decoration-muted-foreground/60')}>{task.text}</p>
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <Pill tone={task.assignee ? 'info' : 'neutral'}>{forWhom(task)}</Pill>
            {open ? (
              <span className={cn(task.overdue && 'font-medium text-sev-unstable')}>{dueLabel(task, at)}</span>
            ) : task.status === 'done' ? (
              <Pill tone="info"><Check aria-hidden />Done by {task.closed?.by} at {clock(task.closed?.at)}</Pill>
            ) : (
              <Pill tone="warn"><CircleSlash aria-hidden />Not needed: {task.reason}</Pill>
            )}
            <span>{origin(task, (t) => TEMPLATE_NAME[t] ?? t)}</span>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1">
          {open && (
            <>
              <Button size="sm" variant="outline" disabled={off} onClick={() => save({ kind: 'done' }, `Done: ${task.text}`)}>
                <Check aria-hidden />
                Done
              </Button>
              <Button size="sm" variant="ghost" disabled={off} aria-expanded={mode === 'not_needed'} onClick={() => setMode(mode === 'not_needed' ? null : 'not_needed')}>
                <CircleSlash aria-hidden />
                Not needed
              </Button>
              <Button size="sm" variant="ghost" disabled={off} aria-expanded={mode === 'reassign'} onClick={openReassign}>
                <UserRoundPen aria-hidden />
                Reassign
              </Button>
              <Button size="sm" variant="ghost" disabled={off} aria-expanded={mode === 'due'} onClick={() => setMode(mode === 'due' ? null : 'due')}>
                <CalendarClock aria-hidden />
                Due time
              </Button>
            </>
          )}
          {task.undo && (
            <Button size="sm" variant="ghost" disabled={off} onClick={() => save({ kind: 'undo' }, `${UNDO_LABEL[task.undo!]}: ${task.text}`)}>
              <RotateCcw aria-hidden />
              {UNDO_LABEL[task.undo]}
            </Button>
          )}
        </div>
      </div>

      {mode === 'not_needed' && (
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            save({ kind: 'not_needed', reason }, `Not needed: ${task.text}`);
          }}
        >
          <div className="min-w-0 flex-1 space-y-1">
            <label htmlFor={`${id}-reason`} className="text-xs font-medium">Why is it not needed?</label>
            <Input id={`${id}-reason`} value={reason} maxLength={MAX_REASON} placeholder="For example: done in the ED before transfer" onChange={(e) => setReason(e.target.value)} />
          </div>
          <Button size="sm" type="submit" disabled={off || !reason.trim()}>Save</Button>
        </form>
      )}
      {mode === 'reassign' && (
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            save(targetBody(), `Reassigned to ${targetName}: ${task.text}`);
          }}
        >
          <div className="min-w-0 flex-1 space-y-1">
            <label htmlFor={`${id}-to`} className="text-xs font-medium">Give it to</label>
            <select id={`${id}-to`} value={target} onChange={(e) => setTarget(e.target.value)} className={cn(fieldClasses, 'h-9 px-2 text-sm')}>
              <option value="">Choose a role or a colleague</option>
              <optgroup label="A role">
                {(['nurse', 'physician'] as const).map((r) => (
                  <option key={r} value={r}>{TASK_ROLE[r]}, whoever has the patient</option>
                ))}
              </optgroup>
              {list && list.length > 0 && (
                <optgroup label="A colleague">
                  {list.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}{p.role_label ? `, ${p.role_label}` : ''}</option>
                  ))}
                </optgroup>
              )}
            </select>
          </div>
          <Button size="sm" type="submit" disabled={off || !target}>Save</Button>
        </form>
      )}
      {mode === 'due' && (
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            if (due) save({ kind: 'due', due }, `Due ${clock(due)}: ${task.text}`);
          }}
        >
          <div className="space-y-1">
            <label htmlFor={`${id}-due`} className="text-xs font-medium">Due at (hospital time, the next time it is this time)</label>
            <Input id={`${id}-due`} type="time" value={time} onChange={(e) => setTime(e.target.value)} className="w-36" />
          </div>
          <Button size="sm" type="submit" disabled={off || !due}>Set due time</Button>
          {task.due && (
            <Button size="sm" type="button" variant="ghost" disabled={off} onClick={() => save({ kind: 'due', due: null }, `No due time: ${task.text}`)}>
              No due time
            </Button>
          )}
        </form>
      )}

      <button
        type="button"
        className="flex items-center gap-1 rounded text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
        aria-expanded={history}
        onClick={() => setHistory(!history)}
      >
        {history ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
        History ({task.history.length})
      </button>
      {history && (
        <ol className="space-y-0.5 border-l pl-3 text-xs text-muted-foreground">
          {task.history.map((s, i) => (
            <li key={i}>{stepLabel(s)}</li>
          ))}
        </ol>
      )}
    </li>
  );
}
