/**
 * The mark itself: one `currentColor` path that has to serve the dark nav tile, the brand panel
 * and the favicon. The last test is the one that matters over time -- the SVG files in `app/`
 * are the same drawing written out again, so nothing but a test keeps them from drifting.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { LOGO_PATH, Logo } from "@/components/Logo";

/**
 * An icon file as the browser sees it: whitespace already collapsed out of the way. Vitest runs
 * with the frontend package as its working directory, which is how these paths are found --
 * `import.meta.url` is a Vite-served URL in this environment, not a path on disk.
 */
function iconSource(relativePath: string) {
  return readFileSync(path.resolve(process.cwd(), relativePath), "utf8").replace(/\s+/g, "");
}

it("is decoration next to the wordmark", () => {
  const { container } = render(<Logo size={20} />);

  const mark = container.querySelector("svg");
  expect(mark).toHaveAttribute("aria-hidden", "true");
  expect(mark).toHaveAttribute("viewBox", "0 0 24 24");
  expect(mark?.querySelector("path")).toHaveAttribute("d", LOGO_PATH);
});

it("carries the brand name when it stands alone", () => {
  render(<Logo title="Excubitor" />);

  const mark = screen.getByRole("img", { name: "Excubitor" });
  expect(mark).not.toHaveAttribute("aria-hidden");
});

it("draws the favicon and the in-app mark from the same path", () => {
  const compact = LOGO_PATH.replace(/\s+/g, "");

  expect(iconSource("app/icon.svg")).toContain(compact);
  expect(iconSource("public/apple-touch-icon.svg")).toContain(compact);
});
