// Adapted from shadcn/ui (MIT, https://github.com/shadcn-ui/ui), built on Radix UI Popover (MIT).
import * as React from 'react';
import * as P from '@radix-ui/react-popover';
import { cn } from '@/lib/utils';

export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;
export const PopoverAnchor = P.Anchor;

export function PopoverContent({ className, sideOffset = 6, align = 'center', ...props }: React.ComponentProps<typeof P.Content>) {
  return (
    <P.Portal>
      <P.Content
        sideOffset={sideOffset}
        align={align}
        className={cn('popover-content z-50 rounded-lg border bg-popover p-4 text-popover-foreground shadow-lg outline-none', className)}
        {...props}
      />
    </P.Portal>
  );
}
