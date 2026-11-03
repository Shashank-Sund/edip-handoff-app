import { ChevronsUpDown } from 'lucide-react';
import { Dropdown, DropdownContent, DropdownItem, DropdownLabel, DropdownSeparator, DropdownTrigger } from '@/components/ui/dropdown';
import { useApp } from '@/hooks/useApp';
import { initials } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { Persona } from '@/lib/types';

function Avatar({ p, className }: { p: Persona; className?: string }) {
  const tone = p.role.startsWith('ed') ? 'bg-primary text-primary-foreground' : 'bg-state-ack/15 text-state-ack';
  return (
    <span className={cn('grid size-8 shrink-0 place-items-center rounded-full text-xs font-semibold', tone, className)} aria-hidden>
      {initials(p.name)}
    </span>
  );
}

export function PersonaMenu({ collapsed }: { collapsed: boolean }) {
  const { config, persona, setPersona } = useApp();
  const groups: [string, Persona[]][] = [
    ['Emergency department', config.personas.filter((p) => p.role.startsWith('ed'))],
    ['Admitting team', config.personas.filter((p) => p.role === 'admitting_physician')],
    ['Receiving units', config.personas.filter((p) => p.role === 'unit_nurse')],
  ];
  const scope = persona.unit ? `${persona.facility} ${persona.unit}` : persona.role_label;
  return (
    <Dropdown>
      <DropdownTrigger
        className={cn(
          'flex w-full items-center gap-2.5 rounded-lg p-1.5 text-left outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50',
          collapsed && 'justify-center',
        )}
        aria-label={`Signed in as ${persona.name}, ${scope}. Switch persona`}
      >
        <Avatar p={persona} />
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{persona.name}</span>
              <span className="block truncate text-xs text-muted-foreground">{persona.unit ? `${persona.role_label}, ${scope}` : scope}</span>
            </span>
            <ChevronsUpDown className="size-4 text-muted-foreground" aria-hidden />
          </>
        )}
      </DropdownTrigger>
      <DropdownContent side={collapsed ? 'right' : 'top'} align="start" className="max-h-[70vh] w-72 overflow-y-auto">
        {config.mode === 'mock' && <p className="px-2 pb-1 pt-1.5 text-xs text-muted-foreground">Demo personas: switch to see each role's view.</p>}
        {groups.map(([label, people], i) => (
          <div key={label}>
            {i > 0 && <DropdownSeparator />}
            <DropdownLabel>{label}</DropdownLabel>
            {people.map((p) => (
              <DropdownItem key={p.id} selected={p.id === persona.id} onSelect={() => setPersona(p.id)}>
                <Avatar p={p} className="size-6 text-[10px]" />
                <span className="min-w-0">
                  <span className="block truncate">{p.name}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {p.unit ? `${p.facility} ${p.unit}, ${p.unit_name?.replace(/^\w+ /, '')}` : p.role_label}
                  </span>
                </span>
              </DropdownItem>
            ))}
          </div>
        ))}
      </DropdownContent>
    </Dropdown>
  );
}
