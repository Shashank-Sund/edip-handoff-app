/**
 * Who owns a patient (app/server/assignment.py): one nurse owner and one physician owner, or Unassigned. A stale
 * owner (no action on the patient within the limit) counts as Unassigned; the server marks it.
 */
import type { CaseSummary, EdRole, Owner, Persona } from './types';

export const ED_ROLES: EdRole[] = ['ed_nurse', 'ed_physician'];
export const ROLE_SHORT: Record<EdRole, string> = { ed_nurse: 'RN', ed_physician: 'MD' };
export const ROLE_OWNER: Record<EdRole, string> = { ed_nurse: 'Nurse owner', ed_physician: 'Physician owner' };

/** The ED roles own patients; the receiving roles and the viewer do not. */
export function edRole(p: Persona): EdRole | null {
  return p.role === 'ed_nurse' || p.role === 'ed_physician' ? p.role : null;
}

/** Someone owns the patient and has not gone stale. */
export const counts = (o: Owner | null | undefined): o is Owner => !!o && !o.stale;

export const ownerOf = (row: CaseSummary, role: EdRole): Owner | null => row.owners?.[role] ?? null;

/** Yours: you own the patient for your role, and it has not gone stale. */
export const isMine = (row: CaseSummary, role: EdRole) => counts(ownerOf(row, role)) && ownerOf(row, role)!.you;

/** Unassigned for your role: no one owns it, or the owner went stale. */
export const isUnassigned = (row: CaseSummary, role: EdRole) => !counts(ownerOf(row, role));

/** The name without the credential the demo personas carry: "Jordan Reyes, RN" is "Jordan Reyes". */
export function shortName(name: string): string {
  return name.replace(/,\s*(RN|MD|DO|NP|PA)$/i, '');
}
