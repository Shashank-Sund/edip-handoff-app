/** What changed since you last looked (app/server/seen.py): words the board marker and the handoff banner use. */
import { ago, clock } from './format';
import { TEMPLATE_NAME } from './labels';
import type { Since, SinceCount, SinceItem } from './types';

const ROLE_OWNER: Record<string, string> = { ed_nurse: 'nurse', ed_physician: 'physician' };
const TASK_DID: Record<string, string> = {
  create: 'added the task',
  done: 'marked done',
  not_needed: 'marked not needed',
  reopen: 'opened again',
  reassign: 'reassigned',
  due: 'changed the due time of',
};

/** "New · 3 changes" on the board row; null when there is nothing new (or the patient was never opened). */
export function sinceLabel(c: SinceCount | undefined): string | null {
  if (!c || c.first_time || !c.count) return null;
  return `New · ${c.count} change${c.count === 1 ? '' : 's'}`;
}

/** "Since you last looked (2 h 10 min ago)". */
export const sinceTitle = (s: Since) => `Since you last looked (${s.last_look ? ago(s.last_look, s.at) : 'never'})`;

const tpl = (id: string) => TEMPLATE_NAME[id] ?? id;

/** One change in plain words, without the time. */
export function sinceText(x: SinceItem): string {
  switch (x.kind) {
    case 'version': {
      const what = x.from == null ? `A first ${tpl(x.template)} draft` : `A new ${tpl(x.template)} draft (version ${x.to})`;
      if (!x.elements.length) return what;
      const names = x.elements.slice(0, 3).map((e) => e.label);
      const more = x.elements.length > 3 ? ` and ${x.elements.length - 3} more` : '';
      return `${what}: ${names.join(', ')}${more} changed`;
    }
    case 'action':
      switch (x.action) {
        case 'sign':
          return `${x.by} signed the ${tpl(x.template)}${x.tasks ? ` (${x.tasks} task${x.tasks === 1 ? '' : 's'} added)` : ''}`;
        case 'ack':
          return `${x.by} accepted the ${tpl(x.template)}`;
        case 'edit':
          return x.restore ? `${x.by} restored the model's text of ${x.label ?? 'an item'} on the ${tpl(x.template)}` : `${x.by} edited ${x.label ?? 'an item'} on the ${tpl(x.template)}`;
        case 'attest':
          return x.removed
            ? `${x.by} removed ${x.option} from ${x.label ?? 'an item'} on the ${tpl(x.template)}`
            : `${x.by} confirmed ${x.label ?? 'an item'} on the ${tpl(x.template)}: ${x.option}`;
      }
      return `${x.by} changed the ${tpl(x.template)}`;
    case 'task': {
      const text = x.text ? `"${x.text}"` : 'a task';
      if (x.undo) return `${x.by} undid a change to ${text}`;
      return x.task === 'reopen' ? `${x.by} opened ${text} again` : `${x.by} ${TASK_DID[x.task] ?? 'changed'} ${text}`;
    }
    case 'owner': {
      const role = ROLE_OWNER[x.role] ?? x.role;
      if (x.owner === 'release') return `${x.by} released the patient (${role})`;
      if (x.owner === 'assign') return `${x.by} assigned the patient to ${x.to} (${role})`;
      if (x.owner === 'handover') return `${x.by} took over the patient from ${x.from ?? 'a colleague'} (${role})`;
      return `${x.by} took the patient (${role})`;
    }
    case 'phase':
      return x.phase === 'arrived' ? 'The patient arrived on the unit' : 'The admission was canceled';
    case 'severity':
      return `Severity changed from ${x.from} to ${x.to}`;
  }
}

/** When the change happened, as the banner shows it. */
export const sinceWhen = (x: SinceItem) => clock(x.at);

/** Where a change is on the handoff page: the template tab to show (if any) and the element id to scroll to. */
export function sinceTarget(x: SinceItem): { template?: string; anchor: string } {
  switch (x.kind) {
    case 'version':
      return { template: x.template, anchor: x.elements.length ? `el-${x.elements[0].id}` : 'signoff' };
    case 'action':
      return { template: x.template, anchor: x.element ? `el-${x.element}` : 'signoff' };
    case 'task':
      return { anchor: 'tasks' };
    case 'owner':
      return { anchor: 'owners' };
    case 'phase':
    case 'severity':
      return { anchor: 'patient' };
  }
}

/**
 * The items of a template that changed since your last look: a newer version's items, edits and attestations, and the
 * illness severity when it changed (its own line in the banner, highlighted on every template).
 */
export function sinceElements(s: Since | null | undefined, template: string): Set<string> {
  const out = new Set<string>();
  for (const x of s?.items ?? []) {
    if (x.kind === 'version' && x.template === template) x.elements.forEach((e) => out.add(e.id));
    if (x.kind === 'action' && x.template === template && x.element) out.add(x.element);
    if (x.kind === 'severity') out.add('E07');
  }
  return out;
}
