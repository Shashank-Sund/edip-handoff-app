import { useState, type ReactNode } from 'react';
import { Menu } from 'lucide-react';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { useTheme } from '@/hooks/useTheme';
import { useApp } from '@/hooks/useApp';
import { readString, writeString } from '@/lib/storage';
import { cn } from '@/lib/utils';
import { ClockControl } from './ClockControl';
import { SidebarBody } from './Sidebar';
import { HelpButton } from '@/components/guide/HelpButton';

const KEY = 'edip.sidebar';

export function Layout({ title, crumbs, actions, help, children }: { title: ReactNode; crumbs?: ReactNode; actions?: ReactNode; help?: string; children: ReactNode }) {
  const { theme, toggle } = useTheme();
  const { config } = useApp();
  const [collapsed, setCollapsed] = useState(() => readString(KEY) === 'collapsed');
  const [open, setOpen] = useState(false);
  const flip = () => {
    writeString(KEY, collapsed ? 'expanded' : 'collapsed');
    setCollapsed(!collapsed);
  };
  return (
    <div className="min-h-dvh">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-card focus:px-3 focus:py-2 focus:shadow">
        Skip to content
      </a>
      <aside className={cn('fixed inset-y-0 left-0 z-30 hidden border-r bg-sidebar transition-[width] duration-200 lg:block', collapsed ? 'w-16' : 'w-60')}>
        <SidebarBody collapsed={collapsed} onToggle={flip} theme={theme} onTheme={toggle} />
      </aside>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" title="Menu" className="w-72 max-w-[85vw] [&>div:first-child]:hidden">
          <SidebarBody collapsed={false} theme={theme} onTheme={toggle} onNavigate={() => setOpen(false)} />
        </SheetContent>
      </Sheet>
      <div className={cn('flex min-h-dvh flex-col transition-[padding] duration-200', collapsed ? 'lg:pl-16' : 'lg:pl-60')}>
        <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/70 sm:px-6">
          <Button variant="ghost" size="icon-sm" className="-ml-1 lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu />
          </Button>
          <div className="min-w-0 flex-1">
            {crumbs && <div className="hidden truncate text-xs text-muted-foreground sm:block">{crumbs}</div>}
            <div className="flex min-w-0 items-center gap-1">
              <h1 className="truncate text-sm font-semibold sm:text-base">{title}</h1>
              {help && <HelpButton topic={help} label="How to use this page" className="size-6" />}
            </div>
          </div>
          {actions}
          {config.mode === 'mock' && (
            <span className="hidden rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground md:inline">Synthetic data</span>
          )}
          <ClockControl />
        </header>
        <main id="main" className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-5 sm:px-6 sm:py-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
