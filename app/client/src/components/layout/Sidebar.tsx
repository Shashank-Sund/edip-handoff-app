import { Activity, Moon, PanelLeftClose, PanelLeftOpen, Sun } from 'lucide-react';
import { WithTooltip } from '@/components/ui/tooltip';
import { PersonaMenu } from './PersonaMenu';
import { NAV } from './nav';
import { linkProps, usePath } from '@/lib/router';
import { cn } from '@/lib/utils';

interface Props {
  collapsed: boolean;
  onToggle?: () => void;
  theme: 'light' | 'dark';
  onTheme: () => void;
  onNavigate?: () => void;
}

export function Logo({ collapsed }: { collapsed?: boolean }) {
  return (
    <a {...linkProps('/')} className="flex items-center gap-2.5 rounded-md outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
      <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground">
        <Activity className="size-4.5" aria-hidden />
      </span>
      {!collapsed && (
        <span className="leading-tight">
          <span className="block text-sm font-semibold">Healthcare Handoff</span>
          <span className="block text-[11px] text-muted-foreground">ED to inpatient</span>
        </span>
      )}
    </a>
  );
}

export function SidebarBody({ collapsed, onToggle, theme, onTheme, onNavigate }: Props) {
  const path = usePath();
  const item = 'flex h-9 items-center gap-3 rounded-lg px-2.5 text-sm font-medium outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring/50';
  return (
    <div className="flex h-full flex-col">
      <div className={cn('flex h-14 shrink-0 items-center px-3', collapsed ? 'justify-center' : 'justify-between')}>
        <Logo collapsed={collapsed} />
      </div>
      <nav aria-label="Main" className="flex-1 space-y-1 px-3 pt-2">
        {NAV.map(({ to, label, Icon, match }) => {
          const active = match(path);
          const link = (
            <a
              key={to}
              {...linkProps(to)}
              onClickCapture={onNavigate}
              aria-current={active ? 'page' : undefined}
              className={cn(
                item,
                collapsed && 'justify-center px-0',
                active ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
              )}
            >
              <Icon className="size-4.5 shrink-0" aria-hidden />
              {collapsed ? <span className="sr-only">{label}</span> : label}
            </a>
          );
          return collapsed ? (
            <WithTooltip key={to} label={label}>
              {link}
            </WithTooltip>
          ) : (
            link
          );
        })}
      </nav>
      <div className="space-y-1 border-t p-3">
        <div className={cn('flex gap-1', collapsed ? 'flex-col items-center' : 'items-center')}>
          <WithTooltip label={theme === 'dark' ? 'Light theme' : 'Dark theme'}>
            <button type="button" onClick={onTheme} className={cn(item, 'w-9 justify-center px-0 text-muted-foreground hover:bg-accent hover:text-foreground')} aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}>
              {theme === 'dark' ? <Sun className="size-4.5" aria-hidden /> : <Moon className="size-4.5" aria-hidden />}
            </button>
          </WithTooltip>
          {onToggle && (
            <WithTooltip label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
              <button type="button" onClick={onToggle} className={cn(item, 'w-9 justify-center px-0 text-muted-foreground hover:bg-accent hover:text-foreground')} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} aria-expanded={!collapsed}>
                {collapsed ? <PanelLeftOpen className="size-4.5" aria-hidden /> : <PanelLeftClose className="size-4.5" aria-hidden />}
              </button>
            </WithTooltip>
          )}
        </div>
        <PersonaMenu collapsed={collapsed} />
      </div>
    </div>
  );
}
