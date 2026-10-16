// @vitest-environment jsdom
/**
 * The platform's one loading indicator. These tests pin the two things other
 * code depends on: the class hook the CSS animation hangs off (.brand-loader
 * and its __mark), and the accessible name a screen reader hears, since the
 * mark itself is decorative. The boot splash in index.html mirrors this markup
 * by hand, so the last test fails if the shared /favicon.svg URL is changed on
 * one side only.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { BrandLoader } from "./BrandLoader";

afterEach(cleanup);

describe("BrandLoader", () => {
  it("shows a visible caption under the mark", () => {
    // The breathing mark alone reads as decoration; the caption says a wait
    // is in progress. index.html's boot splash carries the same word by hand.
    render(<BrandLoader />);
    expect(screen.getByText("Loading...")).toBeTruthy();
  });

  it("announces that it is loading, and says what", () => {
    render(<BrandLoader label="Loading view" />);
    expect(screen.getByRole("status").textContent).toContain("Loading view");
  });

  it("carries the class the shared breathe animation is defined on", () => {
    const { container } = render(<BrandLoader />);
    expect(container.querySelector(".brand-loader__mark")).not.toBeNull();
  });

  it("only fills the viewport when asked, so it can sit inside a page too", () => {
    const { container, unmount } = render(<BrandLoader />);
    expect(container.querySelector(".brand-loader--full")).toBeNull();
    unmount();

    const full = render(<BrandLoader full />);
    expect(full.container.querySelector(".brand-loader--full")).not.toBeNull();
  });

  it("hides the mark from the accessibility tree and loads it by stable URL", () => {
    const { container } = render(<BrandLoader />);
    const mark = container.querySelector("img")!;
    // Not the bundled (hashed) asset: index.html's splash needs this exact path.
    expect(mark.getAttribute("src")).toBe("/favicon.svg");
    expect(mark.getAttribute("aria-hidden")).toBe("true");
  });
});
