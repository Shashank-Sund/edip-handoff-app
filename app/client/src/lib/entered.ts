/** Helpers for values entered in the sign off (code status, allergies, isolation). */

/** The value as the item shows it: "Penicillin: hives" for an allergy with a reaction. */
export function enteredText(x: { value: string; note: string | null }): string {
  return x.note ? `${x.value}: ${x.note}` : x.value;
}

/** Red for anything but full code, as in the patient box. */
export function codeTone(value: string): 'danger' | 'neutral' {
  return /^full code/i.test(value) ? 'neutral' : 'danger';
}

/** Scroll to an item's line in the Sign off panel and put the keyboard on its choices. */
export function goToRule(rule: string) {
  const li = document.getElementById(`rule-${rule}`);
  if (!li) return;
  li.scrollIntoView({ behavior: 'smooth', block: 'center' });
  (li.querySelector<HTMLElement>('[role="radio"][tabindex="0"]') ?? li).focus({ preventScroll: true });
}
