import { ED_ROLES, ROLE_OWNER, ROLE_SHORT, counts, shortName } from '@/lib/owners';
import { cn } from '@/lib/utils';
import type { EdRole, Owner } from '@/lib/types';

/** The owners on a board row or card: "RN: Jordan Reyes · MD: Unassigned". A stale owner reads as Unassigned.
 *  stacked: one line per role, for a narrow table column. */
export function Owners({ owners, stacked, className }: { owners?: Record<EdRole, Owner | null>; stacked?: boolean; className?: string }) {
  if (!owners) return null;
  return (
    <div className={cn('flex text-xs text-muted-foreground', stacked ? 'flex-col gap-0.5' : 'flex-wrap items-center gap-x-1.5', className)}>
      {ED_ROLES.map((role, i) => {
        const o = owners[role];
        return (
          <span key={role} className="whitespace-nowrap">
            {i > 0 && !stacked && <span aria-hidden className="mr-1.5">·</span>}
            <span aria-hidden>{ROLE_SHORT[role]}:</span>
            <span className="sr-only">{ROLE_OWNER[role]}:</span>{' '}
            {counts(o) ? (
              <span className={cn('text-foreground', o.you && 'font-medium')}>
                {shortName(o.name)}
                {o.you && ' (you)'}
              </span>
            ) : (
              <span className="italic">Unassigned</span>
            )}
          </span>
        );
      })}
    </div>
  );
}
