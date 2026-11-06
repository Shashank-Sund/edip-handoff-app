import { BookOpen, ChartLine, FileStack, Handshake, LayoutList } from 'lucide-react';

/** The sidebar pages. roles: shown only to these roles (Take over is for the ED roles that own patients). */
export const NAV: { to: string; label: string; Icon: typeof LayoutList; match: (p: string) => boolean; roles?: string[] }[] = [
  { to: '/', label: 'Board', Icon: LayoutList, match: (p: string) => p === '/' || p.startsWith('/handoff') },
  { to: '/takeover', label: 'Take over', Icon: Handshake, match: (p: string) => p.startsWith('/takeover'), roles: ['ed_nurse', 'ed_physician'] },
  { to: '/analytics', label: 'Analytics', Icon: ChartLine, match: (p: string) => p.startsWith('/analytics') },
  { to: '/templates', label: 'Templates', Icon: FileStack, match: (p: string) => p.startsWith('/templates') },
  { to: '/guide', label: 'How to use', Icon: BookOpen, match: (p: string) => p.startsWith('/guide') || p.startsWith('/about') },
];
