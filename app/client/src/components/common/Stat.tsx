import type { ReactNode } from 'react';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';

export function Stat({ label, value, hint, icon, tone, children }: { label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode; tone?: 'danger' | 'warn' | 'ok'; children?: ReactNode }) {
  const tones = { danger: 'text-sev-unstable', warn: 'text-sev-watcher', ok: 'text-sev-stable' };
  return (
    <Card className="gap-1 p-4 sm:p-5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        {icon && <span className={cn('text-muted-foreground [&>svg]:size-4', tone && tones[tone])}>{icon}</span>}
      </div>
      <p className={cn('text-2xl font-semibold tracking-tight sm:text-[1.75rem]', tone && value !== 0 && tones[tone])}>{value}</p>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {children}
    </Card>
  );
}
