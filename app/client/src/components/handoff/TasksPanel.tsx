import { useEffect, useId, useState } from 'react';
import { ChevronDown, ChevronRight, Info, ListTodo, Lock, Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input, fieldClasses } from '@/components/ui/input';
import { HelpButton } from '@/components/guide/HelpButton';
import { TaskItem, type TaskChangeFn } from '@/components/tasks/TaskItem';
import { useApp } from '@/hooks/useApp';
import { useLoad } from '@/hooks/useLoad';
import { ApiError, api, type NewTask } from '@/lib/api';
import { READ_ONLY } from '@/lib/checks';
import { TASK_ROLE, nextAt } from '@/lib/tasks';
import { cn } from '@/lib/utils';
import type { PatientTasks, Task, TaskPerson } from '@/lib/types';

/** Open tasks shown before "Show all": the overdue and the next ones due. */
const FIRST = 5;

/** Add a task by hand: what, for whom (the nurse, the physician or a colleague), and when. */
function AddTask({ people, off, onAdd }: { people: TaskPerson[]; off: boolean; onAdd: (body: NewTask) => Promise<boolean> }) {
  const { at } = useApp();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [to, setTo] = useState('nurse');
  const [time, setTime] = useState('');
  const id = useId();
  const due = time ? nextAt(time, at) : null;
  if (!open) {
    return (
      <Button size="sm" variant="outline" disabled={off} onClick={() => setOpen(true)}>
        <Plus aria-hidden />
        Add a task
      </Button>
    );
  }
  return (
    <form
      className="space-y-3 rounded-lg border p-3"
      aria-label="Add a task"
      onSubmit={async (e) => {
        e.preventDefault();
        const who = to === 'nurse' || to === 'physician' ? { role: to as 'nurse' | 'physician' } : { to };
        if (await onAdd({ text, ...who, ...(due ? { due } : {}) })) {
          setText('');
          setTo('nurse');
          setTime('');
          setOpen(false);
        }
      }}
    >
      <div className="space-y-1">
        <label htmlFor={`${id}-text`} className="text-xs font-medium">What needs to be done</label>
        <Input id={`${id}-text`} value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} placeholder="For example: recheck potassium after the replacement" autoFocus />
      </div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1 space-y-1">
          <label htmlFor={`${id}-to`} className="text-xs font-medium">For</label>
          <select id={`${id}-to`} value={to} onChange={(e) => setTo(e.target.value)} className={cn(fieldClasses, 'h-9 px-2 text-sm')}>
            <optgroup label="A role">
              {(['nurse', 'physician'] as const).map((r) => (
                <option key={r} value={r}>{TASK_ROLE[r]}, whoever has the patient</option>
              ))}
            </optgroup>
            {people.length > 0 && (
              <optgroup label="A colleague">
                {people.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}{p.role_label ? `, ${p.role_label}` : ''}</option>
                ))}
              </optgroup>
            )}
          </select>
        </div>
        <div className="space-y-1">
          <label htmlFor={`${id}-due`} className="text-xs font-medium">Due at (optional)</label>
          <Input id={`${id}-due`} type="time" value={time} onChange={(e) => setTime(e.target.value)} className="w-36" />
        </div>
      </div>
      <div className="flex gap-2">
        <Button size="sm" type="submit" disabled={off || !text.trim()}>Add task</Button>
        <Button size="sm" type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </form>
  );
}

/**
 * The patient's tasks on the handoff page: the action list items of each signed handoff and the tasks added by
 * hand. Open tasks first, with their actions; done and not needed folded below. A note says when acting is outside
 * your role (someone else has the patient now), and the viewer reads only.
 */
export function TasksPanel({ csn }: { csn: string }) {
  const { at, apiAt, past, persona, version, bump } = useApp();
  const { data, error, setData, retry } = useLoad<PatientTasks>(() => api.patientTasks(csn, apiAt), [csn, at, persona.id, version]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'error' | 'ok'; text: string } | null>(null);
  const [closedOpen, setClosedOpen] = useState(false);
  const [allOpen, setAllOpen] = useState(false);
  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(t);
  }, [notice]);
  const locked = past !== null;

  const save = async (fn: () => Promise<PatientTasks>, ok: string) => {
    if (past) return false; // read only; the server refuses it too
    setBusy(true);
    try {
      setData(await fn());
      setNotice({ tone: 'ok', text: ok });
      bump();
      return true;
    } catch (e) {
      setNotice({ tone: 'error', text: e instanceof ApiError ? e.message : 'Something went wrong.' });
      return false;
    } finally {
      setBusy(false);
    }
  };
  const onChange: TaskChangeFn = (task, body, ok) => save(() => api.changeTask(task.id, body, apiAt), ok);

  const head = (
    <div className="flex items-center justify-between gap-2 border-b bg-muted/30 px-4 py-2.5 sm:px-5">
      <div className="flex items-center gap-2">
        <ListTodo className="size-4 text-muted-foreground" aria-hidden />
        <h3 className="text-sm font-semibold">Tasks</h3>
        {data && (
          <span className="text-xs text-muted-foreground tabular-nums">
            {data.counts.open} open
            {data.counts.overdue > 0 && <span className="font-medium text-sev-unstable"> · {data.counts.overdue} overdue</span>}
          </span>
        )}
        <HelpButton topic="tasks.panel" label="About tasks" className="size-6" />
      </div>
    </div>
  );
  if (error && !data) {
    return (
      <Card className="gap-0 overflow-hidden py-0" role="region" aria-label="Tasks">
        {head}
        <p role="alert" className="px-4 py-3 text-sm text-destructive sm:px-5">
          {error} <button type="button" className="underline" onClick={retry}>Try again</button>
        </p>
      </Card>
    );
  }
  if (!data) return null;
  // Overdue first, then by due time, then the ones without one, each in the order they were created.
  const due = (t: Task) => (t.due ? new Date(t.due).getTime() : Infinity);
  const open = data.tasks.filter((t) => t.status === 'open').sort((a, b) => (due(a) === due(b) ? a.id - b.id : due(a) < due(b) ? -1 : 1));
  const shown = allOpen ? open : open.slice(0, FIRST);
  const closed = data.tasks.filter((t) => t.status !== 'open');
  const canAct = data.access.can_act;
  const item = (t: (typeof data.tasks)[number]) => (
    <TaskItem key={t.id} task={t} at={at} people={data.people} canAct={canAct} locked={locked} busy={busy} onChange={onChange} />
  );

  return (
    <Card id="tasks" tabIndex={-1} className="scroll-mt-20 gap-0 overflow-hidden py-0 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50" role="region" aria-label="Tasks">
      {head}
      {(data.access.note || locked) && (
        <div className="space-y-1 border-b px-4 py-2.5 text-sm sm:px-5">
          {data.access.note && (
            <p className="flex items-start gap-2 text-muted-foreground">
              <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
              {data.access.note}
            </p>
          )}
          {locked && (
            <p className="flex items-start gap-2 text-muted-foreground">
              <Lock className="mt-0.5 size-4 shrink-0" aria-hidden />
              {READ_ONLY}
            </p>
          )}
        </div>
      )}
      {notice && (
        <div role={notice.tone === 'error' ? 'alert' : 'status'} className={cn('border-b px-4 py-2 text-sm sm:px-5', notice.tone === 'error' ? 'bg-destructive/10 text-destructive' : 'bg-sev-stable/10 text-sev-stable')}>
          {notice.text}
        </div>
      )}
      {open.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted-foreground sm:px-5">
          No open tasks. Signing a handoff turns its action list into tasks; you can also add one.
        </p>
      ) : (
        <>
          <ul className="divide-y" aria-label="Open tasks">{shown.map(item)}</ul>
          {open.length > FIRST && (
            <button
              type="button"
              aria-expanded={allOpen}
              onClick={() => setAllOpen(!allOpen)}
              className="w-full border-t px-4 py-2 text-left text-xs font-medium text-primary outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50 sm:px-5"
            >
              {allOpen ? 'Show fewer' : `Show all ${open.length} open tasks`}
            </button>
          )}
        </>
      )}
      {closed.length > 0 && (
        <div className="border-t">
          <button
            type="button"
            aria-expanded={closedOpen}
            onClick={() => setClosedOpen(!closedOpen)}
            className="flex w-full items-center gap-1 px-4 py-2.5 text-left text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 sm:px-5"
          >
            {closedOpen ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
            Done and not needed ({closed.length})
          </button>
          {closedOpen && <ul className="divide-y border-t" aria-label="Done and not needed">{closed.map(item)}</ul>}
        </div>
      )}
      {canAct && (
        <div className="border-t px-4 py-3 sm:px-5">
          <AddTask people={data.people} off={locked || busy} onAdd={(body) => save(() => api.addTask(csn, body, apiAt), `Task added: ${body.text.trim()}`)} />
        </div>
      )}
    </Card>
  );
}
