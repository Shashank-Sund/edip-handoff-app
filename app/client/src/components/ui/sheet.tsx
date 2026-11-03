// Adapted from shadcn/ui (MIT, https://github.com/shadcn-ui/ui), built on Radix UI Dialog (MIT).
import * as React from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';

export const Sheet = Dialog.Root;
export const SheetTrigger = Dialog.Trigger;
export const SheetClose = Dialog.Close;

export function SheetContent({
  side = 'right',
  className,
  children,
  title,
  description,
  ...props
}: React.ComponentProps<typeof Dialog.Content> & { side?: 'right' | 'left'; title: string; description?: string }) {
  return (
    <Dialog.Portal>
      <Dialog.Overlay className="sheet-overlay fixed inset-0 z-50 bg-black/40 backdrop-blur-[1px]" />
      <Dialog.Content
        className={cn(
          'sheet-content fixed inset-y-0 z-50 flex w-full flex-col bg-card shadow-xl outline-none',
          side === 'right' ? 'right-0 border-l sm:max-w-lg' : 'left-0 border-r sm:max-w-xs',
          className,
        )}
        data-side={side}
        {...props}
      >
        <div className="flex items-start justify-between gap-4 border-b px-5 py-4">
          <div className="min-w-0">
            <Dialog.Title className="truncate text-base font-semibold">{title}</Dialog.Title>
            {description ? (
              <Dialog.Description className="mt-0.5 text-sm text-muted-foreground">{description}</Dialog.Description>
            ) : (
              <Dialog.Description className="sr-only">{title}</Dialog.Description>
            )}
          </div>
          <Dialog.Close className="-mr-1 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 outline-none">
            <X className="size-4" aria-hidden />
            <span className="sr-only">Close</span>
          </Dialog.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </Dialog.Content>
    </Dialog.Portal>
  );
}
