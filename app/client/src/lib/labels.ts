/** Words shared by the screens and the guide, so the two always say the same. */

/** What each handoff tab is and who signs and accepts it. */
export const TAB_HELP: Record<string, string> = {
  sbar_nurse: 'Nurse handoff. The ED nurse signs it; the receiving nurse accepts it.',
  ipass_physician: 'Physician handoff. The ED physician signs it; the admitting physician accepts it.',
  custom_sea58: 'The short format with what every handoff needs. Either ED clinician signs it; either receiver accepts it.',
};

export const TEMPLATE_SHORT: Record<string, string> = { sbar_nurse: 'SBAR', ipass_physician: 'I-PASS', custom_sea58: 'Minimum' };
export const TEMPLATE_NAME: Record<string, string> = { sbar_nurse: 'SBAR', ipass_physician: 'I-PASS', custom_sea58: 'Joint Commission minimum' };
