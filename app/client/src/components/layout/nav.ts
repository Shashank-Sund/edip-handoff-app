import { BookOpen, ChartLine, FileStack, LayoutList } from 'lucide-react';

export const NAV = [
  { to: '/', label: 'Board', Icon: LayoutList, match: (p: string) => p === '/' || p.startsWith('/handoff') },
  { to: '/analytics', label: 'Analytics', Icon: ChartLine, match: (p: string) => p.startsWith('/analytics') },
  { to: '/templates', label: 'Templates', Icon: FileStack, match: (p: string) => p.startsWith('/templates') },
  { to: '/about', label: 'How it works', Icon: BookOpen, match: (p: string) => p.startsWith('/about') },
];
