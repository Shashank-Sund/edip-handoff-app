import { ChevronDown, UserMinus, UserPlus, Users } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Dropdown, DropdownContent, DropdownItem, DropdownLabel, DropdownTrigger } from '@/components/ui/dropdown';
import { HelpButton } from '@/components/guide/HelpButton';
import { useLoad } from '@/hooks/useLoad';
import { api, type OwnerBody } from '@/lib/api';
import { duration, minutesBetween, clock } from '@/lib/format';
import { ED_ROLES, ROLE_OWNER, counts, edRole, shortName } from '@/lib/owners';
import { cn } from '@/lib/utils';
import type { CaseSummary, Owner, Persona } from '@/lib/types';

function Who({ o, at }: { o: Owner | null; at: string }) {
  const stale = o?.stale ? o : null;
  if (counts(o)) {
    return (
      <span className={cn('font-medium', o.you && 'text-primary')}>
        {shortName(o.name)}
        {o.you && ' (you)'}
      </span>
    );
  }
  return (
    <span>
      <span className="italic text-muted-foreground">Unassigned</span>
      {stale && (
        <span className="text-muted-foreground">
          {' '}
          ({shortName(stale.name)} has not acted on this patient for {duration(Math.round(minutesBetween(stale.last_active, at)))})
        </span>
      )}
    </span>
  );
}

/**
 * Who owns the patient, for the nurse and the physician, and for an ED clinician the buttons to Take the patient,
 * Assign it to a colleague in the same role, or Release it. Acting on a patient someone else owns is allowed: a
 * quiet note says who it is assigned to.
 */
export function OwnerLine({ s, persona, at, locked, busy, onOwn }: {
  s: CaseSummary;
  persona: Persona;
  at: string;
  locked: boolean;
  busy: boolean;
  onOwn: (body: OwnerBody, ok: string) => void;
}) {
  const role = edRole(persona);
  const { data: colleagues } = useLoad(() => (role ? api.colleagues() : Promise.resolve([])), [persona.id]);
  if (!s.owners) return null;
  const mine = role ? s.owners[role] : null;
  const yours = counts(mine) && mine.you;
  const others = (colleagues ?? []).filter((c) => !c.you && !(counts(mine) && c.id === mine.id));
  const boarding = s.phase === 'boarding';
  const off = locked || busy || !boarding;
  return (
    <Card id="owners" tabIndex={-1} role="group" className="scroll-mt-20 gap-2 px-4 py-3 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 sm:px-5" aria-label="Who owns this patient">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
          <Users className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          {ED_ROLES.map((r) => (
            <span key={r}>
              <span className="text-muted-foreground">{ROLE_OWNER[r]}: </span>
              <Who o={s.owners![r]} at={at} />
            </span>
          ))}
          <HelpButton topic="handoff.owner" label="About owners" className="size-6" />
        </div>
        {role && (
          <div className="flex flex-wrap items-center gap-1.5">
            {!yours && (
              <Button size="sm" variant="outline" disabled={off} onClick={() => onOwn({ kind: 'claim' }, 'You took this patient.')}>
                <UserPlus aria-hidden />
                Take
              </Button>
            )}
            <Dropdown>
              <DropdownTrigger asChild disabled={off || others.length === 0}>
                <Button size="sm" variant="outline" disabled={off || others.length === 0}>
                  Assign to a colleague
                  <ChevronDown aria-hidden />
                </Button>
              </DropdownTrigger>
              <DropdownContent align="end">
                <DropdownLabel>{persona.role_label}s</DropdownLabel>
                {others.map((c) => (
                  <DropdownItem key={c.id} onSelect={() => onOwn({ kind: 'assign', to: c.id }, `Assigned to ${c.name}.`)}>
                    {c.name}
                  </DropdownItem>
                ))}
              </DropdownContent>
            </Dropdown>
            {mine && mine.you && (
              <Button size="sm" variant="ghost" disabled={off} onClick={() => onOwn({ kind: 'release' }, 'Released. The patient is Unassigned for your role.')}>
                <UserMinus aria-hidden />
                Release
              </Button>
            )}
          </div>
        )}
      </div>
      {role && counts(mine) && !mine.you && (
        <p className="text-muted-foreground">
          Assigned to {mine.name} since {clock(mine.since)}. You can still act on this handoff; it is recorded as outside your assignment.
        </p>
      )}
      {role && !boarding && <p className="text-muted-foreground">The patient has left the ED, so owners no longer change.</p>}
    </Card>
  );
}
