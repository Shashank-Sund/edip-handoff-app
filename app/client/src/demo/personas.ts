/** Port of app/server/personas.py: demo personas and what each role may do. */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { cmpStr } from './py';

export const SIGNS: Record<string, string[]> = { ed_nurse: ['sbar_nurse', 'custom_sea58'], ed_physician: ['ipass_physician', 'custom_sea58'] };
export const ACKS: Record<string, string[]> = { unit_nurse: ['sbar_nurse', 'custom_sea58'], admitting_physician: ['ipass_physician', 'custom_sea58'] };
const DEFAULT_TEMPLATE: Record<string, string> = {
  ed_nurse: 'sbar_nurse', ed_physician: 'ipass_physician', unit_nurse: 'sbar_nurse', admitting_physician: 'ipass_physician', viewer: 'sbar_nurse',
};
const ROLE_LABEL: Record<string, string> = {
  ed_nurse: 'ED nurse', ed_physician: 'ED physician', unit_nurse: 'Receiving nurse', admitting_physician: 'Admitting physician', viewer: 'Viewer (read only)',
};

export function demoPersonas(units: any[]): any[] {
  const people: any[] = [
    { id: 'ed-rn', name: 'Jordan Reyes, RN', role: 'ed_nurse', unit: null, facility: null },
    { id: 'ed-md', name: 'Alex Chen, MD', role: 'ed_physician', unit: null, facility: null },
    { id: 'hosp-md', name: 'Morgan Patel, MD', role: 'admitting_physician', unit: null, facility: null },
  ];
  const names = ['Sam Okafor, RN', 'Riley Novak, RN', 'Casey Lindqvist, RN', 'Taylor Brooks, RN', 'Jamie Alvarez, RN',
    'Drew Kowalski, RN', 'Avery Nguyen, RN', 'Quinn Mahoney, RN'];
  const sorted = [...units].sort((a, b) => cmpStr(a.facility, b.facility) || cmpStr(a.code, b.code));
  sorted.forEach((u, i) => {
    people.push({ id: `unit-${u.facility}-${u.code}`.toLowerCase(), name: names[i % names.length], role: 'unit_nurse', unit: u.code,
      facility: u.facility, unit_name: u.name });
  });
  people.push({ id: 'viewer', name: 'Viewer', role: 'viewer', unit: null, facility: null });
  for (const p of people) {
    p.role_label = ROLE_LABEL[p.role];
    p.default_template = DEFAULT_TEMPLATE[p.role];
    p.can_sign = [...(SIGNS[p.role] ?? [])];
    p.can_ack = [...(ACKS[p.role] ?? [])];
  }
  return people;
}

export function sees(person: any, facility: string | null, destUnit: string | null): boolean {
  if (person.role !== 'unit_nurse') return true;
  const pf = person.facility ?? null;
  return destUnit === person.unit && (pf === null || pf === facility);
}
