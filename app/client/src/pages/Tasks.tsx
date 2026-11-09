import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Info, Lock } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Layout } from '@/components/layout/Layout';
import { SeverityBadge } from '@/components/common/badges';
import { EmptyState, ErrorState, PageSkeleton } from '@/components/common/states';
import { HelpButton } from '@/components/guide/HelpButton';
import { TaskItem, type TaskChangeFn } from '@/components/tasks/TaskItem';
import { useApp } from '@/hooks/useApp';
import { useLoad } from '@/hooks/useLoad';
import { ApiError, api } from '@/lib/api';
import { READ_ONLY } from '@/lib/checks';
import { linkProps } from '@/lib/router';
import { GROUP_TITLE, TASK_ROLE } from '@/lib/tasks';
import { cn } from '@/lib/utils';
import type { TaskRow, TasksPage } from '@/lib/types';

type Scope = 'mine' | 'all';

/** The patient a task belongs to, above the task: name, severity, where they are or are going. */
function PatientLine({ t }: { t: TaskRow }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
      <a {...linkProps(`/handoff/${t.csn}`)} className="font-medium text-foreground hover:underline">
        {t.patient.name}
      </a>
      <SeverityBadge value={t.severity} />
      <span>
        {t.phase === 'arrived' ? `On ${t.facility} ${t.unit ?? 'the unit'}` : `In the ED, going to ${t.facility} ${t.unit ?? '(bed pending)'}`}
      </span>
      {!t.in_role && <span className="italic">· someone else has this patient now</span>}
    </div>
  );
}

/**
 * Tasks across patients: yours (for your role on the patients you have, and those given to you) or every task on
 * your board, grouped Overdue, Due in the next 2 hours, Later and No time, with the recently completed folded below.
 */
export default function Tasks() {
  const { at, apiAt, past, persona, version, bump } = useApp();
  const [scope, setScope] = useState<Scope>(persona.role === 'viewer' ? 'all' : 'mine');
  useEffect(() => setScope(persona.role === 'viewer' ? 'all' : 'mine'), [persona.id, persona.role]);
  const { data, error, retry } = useLoad<TasksPage>(() => api.tasks(scope, apiAt), [scope, at, persona.id, version]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'error' | 'ok'; text: string } | null>(null);
  const [doneOpen, setDoneOpen] = useState(false);
  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(t);
  }, [notice]);
  const locked = past !== null;
  const canAct = persona.role !== 'viewer';
  const title = 'Tasks';

  const onChange: TaskChangeFn = async (task, body, ok) => {
    if (past) return false; // read only; the server refuses it too
    setBusy(true);
    try {
      await api.changeTask(task.id, body, apiAt);
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

  if (error && !data) return <Layout title={title} help="tasks"><ErrorState message={error} onRetry={retry} /></Layout>;
  if (!data) return <Layout title={title} help="tasks"><PageSkeleton /></Layout>;

  const item = (t: TaskRow) => (
    <TaskItem
      key={t.id}
      task={t}
      at={at}
      people={() => api.patientTasks(t.csn, apiAt).then((p) => p.people)}
      canAct={canAct}
      locked={locked}
      busy={busy}
      onChange={onChange}
      extra={<PatientLine t={t} />}
    />
  );
  const role = data.role ? TASK_ROLE[data.role].toLowerCase() : null;

  return (
    <Layout title={title} crumbs={persona.name} help="tasks">
      <div className="max-w-5xl space-y-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted-foreground">
            {scope === 'mine'
              ? `Your open tasks: the ${role ?? 'tasks'} tasks of the patients you have now${persona.role === 'ed_nurse' || persona.role === 'ed_physician' ? ' (the ones you own)' : ''}, and any given to you by name.`
              : 'Every open task of the patients on your board.'}
          </p>
          <div className="flex items-center gap-1">
            <div role="tablist" aria-label="Whose tasks" className="inline-flex rounded-lg bg-muted p-0.5">
              {(['mine', 'all'] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  role="tab"
                  aria-selected={scope === s}
                  onClick={() => setScope(s)}
                  className={cn('whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50', scope === s ? 'bg-card text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground')}
                >
                  {s === 'mine' ? 'My tasks' : 'Everyone'}
                </button>
              ))}
            </div>
            <HelpButton topic="tasks.page" label="About this page" className="size-6" />
          </div>
        </div>
        {locked && (
          <p className="flex items-start gap-2 rounded-lg border bg-muted/50 px-4 py-2.5 text-sm">
            <Lock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
            {READ_ONLY}
          </p>
        )}
        {!canAct && (
          <p className="flex items-start gap-2 rounded-lg border bg-muted/50 px-4 py-2.5 text-sm">
            <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
            The viewer role can read tasks but not change them.
          </p>
        )}
        {notice && (
          <div role={notice.tone === 'error' ? 'alert' : 'status'} className={notice.tone === 'error' ? 'rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-2.5 text-sm text-destructive' : 'rounded-lg border border-sev-stable/30 bg-sev-stable/10 px-4 py-2.5 text-sm text-sev-stable'}>
            {notice.text}
          </div>
        )}
        {data.counts.open === 0 && (
          <Card>
            <EmptyState title={scope === 'mine' ? 'No open tasks for you' : 'No open tasks'}>
              Tasks come from the action list when a handoff is signed, or are added by hand on the handoff page.
              {scope === 'mine' && ' Everyone shows every task on your board.'}
            </EmptyState>
          </Card>
        )}
        {data.groups.filter((g) => g.tasks.length).map((g) => (
          <Card key={g.id} className="gap-0 overflow-hidden py-0" role="region" aria-label={GROUP_TITLE[g.id]}>
            <div className="flex items-center gap-2 border-b bg-muted/30 px-4 py-2.5 sm:px-5">
              <h2 className={cn('text-sm font-semibold', g.id === 'overdue' && 'text-sev-unstable')}>{GROUP_TITLE[g.id]}</h2>
              <span className="text-xs text-muted-foreground tabular-nums">{g.tasks.length}</span>
              {g.id === 'overdue' && <HelpButton topic="tasks.overdue" label="About overdue" className="size-6" />}
            </div>
            <ul className="divide-y">{g.tasks.map(item)}</ul>
          </Card>
        ))}
        {data.done.length > 0 && (
          <Card className="gap-0 overflow-hidden py-0">
            <button
              type="button"
              aria-expanded={doneOpen}
              onClick={() => setDoneOpen(!doneOpen)}
              className="flex w-full items-center gap-1 px-4 py-2.5 text-left text-sm font-semibold outline-none hover:bg-muted/40 focus-visible:ring-[3px] focus-visible:ring-ring/50 sm:px-5"
            >
              {doneOpen ? <ChevronDown className="size-4" aria-hidden /> : <ChevronRight className="size-4" aria-hidden />}
              Recently completed
              <span className="text-xs font-normal text-muted-foreground tabular-nums">{data.done.length} in the last 12 hours</span>
            </button>
            {doneOpen && <ul className="divide-y border-t">{data.done.map(item)}</ul>}
          </Card>
        )}
      </div>
    </Layout>
  );
}
