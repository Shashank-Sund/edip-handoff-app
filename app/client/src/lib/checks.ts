/**
 * The server's rules for what a clinician types (app/server/logic.py), checked as they type so a button can say
 * why it is not ready yet. The server checks again and has the last word.
 */
import type { CaseTemplate } from './types';

export const MAX_TEXT = 2000;

/**
 * Whether the ED can still edit, attest and sign: while the patient boards, for an addendum, or after the transfer
 * when the ED never signed or signed an older version.
 */
export function canChange(tpl: CaseTemplate, phase: string): boolean {
  if (phase === 'boarding' || tpl.row.status === 'addendum') return true;
  const s = tpl.workflow.signed;
  return phase === 'arrived' && (!s || s.version < tpl.row.version);
}

const letters = (s: string) => s.match(/\p{L}/gu) ?? [];

/** At least a sentence: three words, fifteen letters, and not one character repeated. */
export function readbackOk(text: string): boolean {
  const t = text.trim();
  const ls = letters(t);
  const words = t.split(/\s+/).filter((w) => letters(w).length > 0);
  return words.length >= 3 && ls.length >= 15 && new Set(ls.map((c) => c.toLowerCase())).size >= 2;
}

/** Why Accept handoff is not ready yet, or null when it is. */
export function readbackProblem(text: string): string | null {
  if (!text.trim()) return 'Write your read back to accept.';
  if (text.trim().length > MAX_TEXT) return 'Keep the read back under 2,000 characters.';
  if (!readbackOk(text)) return 'Write at least a short sentence: the patient, the key risk and what you will do first.';
  return null;
}

/** At least 3 digits, or an extension like x4471. */
export function callbackOk(cb: string): boolean {
  const t = cb.trim();
  return t.length <= 40 && ((t.match(/[0-9]/g) ?? []).length >= 3 || /^(x|ext\.?) ?[0-9]{2,}$/i.test(t));
}
