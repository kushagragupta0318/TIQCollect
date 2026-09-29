// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { escapeHtml } from "./html";

const HOSTILE = [
  "<script>alert(1)</script>",
  '<img src=x onerror="alert(1)">',
  `" onmouseover="alert(1)`,
  "' onfocus='alert(1)",
  "Tom & Jerry's <b>bold</b>",
];

describe("escapeHtml", () => {
  it("encodes all five HTML metacharacters", () => {
    expect(escapeHtml(`<>&"'`)).toBe("&lt;&gt;&amp;&quot;&#39;");
  });

  it("leaves ordinary text alone and stringifies numbers and empties", () => {
    expect(escapeHtml("Priya Sharma · EMP0006")).toBe("Priya Sharma · EMP0006");
    expect(escapeHtml(42)).toBe("42");
    expect(escapeHtml(0)).toBe("0");
    expect(escapeHtml(null)).toBe("");
    expect(escapeHtml(undefined)).toBe("");
  });

  it("renders hostile text as text, in element content and in a quoted attribute", () => {
    for (const text of HOSTILE) {
      const host = document.createElement("div");
      host.innerHTML = `<b>${escapeHtml(text)}</b><span title="${escapeHtml(text)}">x</span>`;
      expect(host.querySelector("script, img, [onmouseover], [onfocus]")).toBeNull();
      expect(host.querySelector("b")?.textContent).toBe(text);
      expect(host.querySelector("span")?.getAttribute("title")).toBe(text);
      expect(host.children).toHaveLength(2);
    }
  });
});
