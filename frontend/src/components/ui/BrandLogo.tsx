// ─── CHANGELOG (prototype → product) ────────────────────────────────────────
// 2026-08-18 — New file. The TransOrg mark replaces the lucide ShieldCheck that
//   stood in for a logo. It is a component rather than five copies of an <img>
//   because that is exactly how the placeholder drifted: the mark was written
//   out by hand in LoginPage, AgentLayout (twice), ManagerLayout and
//   LandingPage, so changing the logo meant finding all five. Now there is one.
//
//   Note ShieldCheck is still used elsewhere and must stay — in RecordVisitPage,
//   AgentCaseDetailPage and further down LandingPage it means "verified /
//   compliant", which is an icon, not a brand.
//
//   The asset is a vector trace of a JPG, so it arrived with an opaque white
//   backdrop: its outer blue ring was that backdrop showing through a cut-out in
//   a white layer above it, which is why simply deleting the white also deleted
//   the ring. It is now clipped to that same cut-out silhouette (`clipPath
//   #tqmark` in the file), which drops the backdrop and keeps the ring. Verified
//   on white, on the #F4F5F7 app background and on the #EFF6FF chip tint.
// ──────────────────────────────────────────────────────────────────────────
import transorgLogo from "@/assets/transorg-logo.svg";

export function BrandLogo({ size = 34, className }: { size?: number; className?: string }) {
  return (
    <img
      src={transorgLogo}
      alt=""
      aria-hidden="true"
      width={size}
      height={size}
      // The source is 314×290, so contain rather than letting it stretch square.
      className={`shrink-0 object-contain ${className ?? ""}`}
      style={{ width: size, height: size }}
    />
  );
}

export default BrandLogo;
