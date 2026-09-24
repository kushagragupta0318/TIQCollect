import { describe, expect, it } from "vitest";
import { cva } from "./cva";
import { cn } from "./cn";
import { buttonVariants } from "../ui/buttonVariants";
import { badgeVariants } from "../ui/badgeVariants";

const demo = cva("base", {
  variants: {
    tone: { quiet: "t-quiet", loud: "t-loud" },
    size: { sm: "s-sm", lg: "s-lg" },
    flag: { true: "f-on", false: "f-off" },
  },
  defaultVariants: { tone: "quiet", size: "sm", flag: false },
});

describe("cva — class-variance-authority 0.7.1 resolution rules", () => {
  it("applies defaults for undefined props", () => {
    expect(demo()).toBe("base t-quiet s-sm f-off");
    expect(demo({ tone: undefined })).toBe("base t-quiet s-sm f-off");
  });

  it("an explicit value wins over the default", () => {
    expect(demo({ tone: "loud", size: "lg" })).toBe("base t-loud s-lg f-off");
  });

  it("null switches a variant off, default included", () => {
    expect(demo({ tone: null })).toBe("base s-sm f-off");
  });

  it("booleans match the 'true' / 'false' keys", () => {
    expect(demo({ flag: true })).toBe("base t-quiet s-sm f-on");
  });

  it("appends class then className, last", () => {
    expect(demo({ class: "c1", className: "c2" })).toBe("base t-quiet s-sm f-off c1 c2");
  });

  it("works with no variants at all", () => {
    expect(cva("only")({ className: "extra" })).toBe("only extra");
  });
});

describe("the ported CVA tables produce CC's class strings", () => {
  it("default button: outlined primary on white, 40px", () => {
    const cls = buttonVariants();
    expect(cls).toContain("border-primary text-primary hover:bg-accent");
    expect(cls).toContain("h-10 px-4 py-2 [&_svg]:size-4");
    expect(cls).toContain("bg-card");
    expect(cls).not.toContain("bg-primary ");
  });

  it("every button variant is outlined — none is filled", () => {
    for (const variant of ["default", "destructive", "outline", "secondary", "ghost", "link", "success", "warning"] as const) {
      expect(buttonVariants({ variant })).toMatch(/(^| )border-/);
      expect(buttonVariants({ variant })).toContain("bg-card");
    }
  });

  it("button sizes", () => {
    expect(buttonVariants({ size: "xs" })).toContain("h-8 px-2.5 text-[12px] [&_svg]:size-3");
    expect(buttonVariants({ size: "icon" })).toContain("h-10 w-10 [&_svg]:size-4");
  });

  it("badge soft variants map to the premium classes", () => {
    expect(badgeVariants({ variant: "softDanger" })).toContain("badge-premium-red");
    expect(badgeVariants()).toContain("bg-accent");
  });

  it("cn lets a caller's class override a variant's (twMerge)", () => {
    expect(cn(buttonVariants({ size: "sm" }), "h-12")).not.toContain("h-9");
  });
});
