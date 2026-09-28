// The component gallery (/bank/_gallery) shows an invented sample book. It is
// a parity and development surface, so — like /simulator in App.tsx — it is on
// in the Vite dev server and off in a production build unless
// VITE_ENABLE_BANK_GALLERY=1. Off, the route and every link to it are absent.
//
// The lazy import sits HERE, in the same module as the condition, on purpose:
// Vite folds `import.meta.env.DEV || …` to `false` in a production build, and
// only a ternary it can see in this module lets the bundler drop the dynamic
// import — and with it the gallery chunk and its sample data — from the
// build. Measured 2026-09-24: with the flag imported from another module the
// chunk (85.7 kB, every sample figure in it) was still emitted; declared here
// it is not. (The simulator's chunk is emitted in production for exactly that
// reason — App.tsx references it outside the ternary.)
import { lazy } from "react";

export const BANK_GALLERY_ENABLED: boolean =
  import.meta.env.DEV || import.meta.env.VITE_ENABLE_BANK_GALLERY === "1";

export const BankComponentGalleryPage = BANK_GALLERY_ENABLED ? lazy(() => import("./pages/BankComponentGalleryPage")) : null;

export const BANK_GALLERY_PATH = "/bank/_gallery";
