// A navigable stand-in for every bank screen that is not built yet, made from
// the ported page template (spec §5.1) so the shell looks right end to end.
// It says plainly that the screen is not built and which tasks build it, and
// claims nothing live: no live dot, no figures.
import { Link } from "react-router";
import { ExecutiveHeader, PageRoot } from "../components/PageTemplate";
import { Headline, Panel } from "../components/analytics";
import { BANK_GALLERY_ENABLED, BANK_GALLERY_PATH } from "../galleryFlag";
import { BANK_SECTIONS, type BankNavItem } from "../layout/navigation";

export function BankPlaceholderPage({ item }: { item: BankNavItem }) {
  const section = BANK_SECTIONS.find((s) => s.items.includes(item));
  const Icon = item.icon;
  return (
    <PageRoot>
      <ExecutiveHeader
        title={item.name}
        meta={[section?.label ?? "Bank portal", `Tasks ${item.tasks}`]}
        scopeNote={item.summary}
      />
      <Panel title="Not built yet" hint={`STANDALONE-TASKS.md · ${item.tasks}`}>
        <div className="flex items-start gap-4">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-inner bg-accent" aria-hidden="true">
            <Icon className="size-[18px] text-primary" />
          </span>
          <div className="space-y-3">
            <Headline text={`${item.name} lands with ${item.tasks}. The shell, tokens and components it will be built from are in place.`} />
            {BANK_GALLERY_ENABLED && (
              <Link to={BANK_GALLERY_PATH} className="inline-flex text-[12px] font-semibold text-primary hover:underline underline-offset-4">
                See the component gallery (sample data)
              </Link>
            )}
          </div>
        </div>
      </Panel>
    </PageRoot>
  );
}
