// Adapted from shadcn/ui (MIT, https://github.com/shadcn-ui/ui), built on Radix UI Dropdown Menu (MIT).
import * as React from 'react';
import * as Menu from '@radix-ui/react-dropdown-menu';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

export const Dropdown = Menu.Root;
export const DropdownTrigger = Menu.Trigger;

export function DropdownContent({ className, sideOffset = 6, ...props }: React.ComponentProps<typeof Menu.Content>) {
  return (
    <Menu.Portal>
      <Menu.Content
        sideOffset={sideOffset}
        className={cn('popover-content z-50 min-w-56 overflow-hidden rounded-lg border bg-popover p-1 text-popover-foreground shadow-lg', className)}
        {...props}
      />
    </Menu.Portal>
  );
}

export function DropdownLabel({ className, ...props }: React.ComponentProps<typeof Menu.Label>) {
  return <Menu.Label className={cn('px-2 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground', className)} {...props} />;
}

export function DropdownSeparator() {
  return <Menu.Separator className="-mx-1 my-1 h-px bg-border" />;
}

export function DropdownItem({ className, selected, children, ...props }: React.ComponentProps<typeof Menu.Item> & { selected?: boolean }) {
  return (
    <Menu.Item
      className={cn(
        'relative flex cursor-default select-none items-center gap-2 rounded-md py-1.5 pl-2 pr-8 text-sm outline-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground data-[disabled]:opacity-50',
        className,
      )}
      {...props}
    >
      {children}
      {selected && <Check className="absolute right-2 size-4 text-primary" aria-hidden />}
    </Menu.Item>
  );
}
