// The marker every composite shows when it is rendering invented figures (the
// gallery's sample book). Not from CC — CC never shows sample data. It lives
// inside each composite rather than only in a page header because the drill
// drawer and the workspace portal out of the page: a header marker cannot be
// seen from inside them. Styled as CC's warning badge (#FDF0DC / #B54708).
import { SAMPLE_DATA_LABEL } from "./sampleData";

export function SampleDataNote({ label = SAMPLE_DATA_LABEL }: { label?: string }) {
  return (
    <span
      role="note"
      className="inline-flex items-center gap-1.5 rounded-full bg-[#FDF0DC] px-2.5 py-1 text-[11px] font-semibold text-[#B54708] whitespace-nowrap"
    >
      <span className="size-1.5 rounded-full bg-[#F79009]" aria-hidden="true" />
      {label}
    </span>
  );
}
