/**
 * The small square before a section title. Mnemonic letters (S, B, A, R; I, P, A, S) show as they are; a longer
 * section id such as HDR or SAFE is not a word clinicians read, so the square shows the label's first letter.
 */
export function SectionBadge({ id, label }: { id: string; label: string }) {
  const text = id.length <= 2 ? id : (label.trim()[0] ?? id[0]).toUpperCase();
  return (
    <span className="grid size-6 place-items-center rounded-md bg-primary/10 text-[11px] font-bold text-primary" aria-hidden>
      {text}
    </span>
  );
}
