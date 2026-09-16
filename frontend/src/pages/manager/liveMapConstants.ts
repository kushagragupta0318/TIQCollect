/**
 * Shared by ManagerLiveMapPage and liveMapNavigate so "stale" means one
 * thing on the page — the list's amber age, the marker's popup and the
 * Navigate button's wording all read this constant. It lived inline in the
 * page until 2026-09-16; a second copy in the navigate helper would have
 * been the two-definitions drift this repo keeps finding.
 */

/** A fix older than this is shown as stale: ten minutes. */
export const STALE_AFTER_S = 600;
