/**
 * The chrome around every page: the brand block and the tabs, which are the two things a role
 * can change about it. `next/navigation` is stubbed because the same component has to look like
 * a header on the dashboard and like nothing at all on the sign-in page.
 */

import { screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Nav } from "@/components/Nav";
import { renderPage, stubApi } from "@/test/support";

let pathname = "/";

vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={typeof href === "string" ? href : "#"}>{children}</a>
  ),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  pathname = "/";
});

it("shows the mark, the wordmark and the CloudOps AI descriptor", async () => {
  stubApi("admin", () => undefined);
  const { container } = renderPage(<Nav />);

  const brand = screen.getByRole("link", { name: /Excubitor/ });
  expect(brand).toHaveAttribute("href", "/");
  expect(brand).toHaveAccessibleName("Excubitor CloudOps AI");
  expect(container.querySelector("svg")).not.toBeNull();

  await screen.findByText("CloudOps AI");
});

it("offers the Users tab to an admin and none to a viewer", async () => {
  stubApi("admin", () => undefined);
  const admin = renderPage(<Nav />);
  expect(await screen.findByRole("link", { name: /Users/ })).toBeInTheDocument();
  admin.unmount();

  stubApi("viewer", () => undefined);
  renderPage(<Nav />);
  await screen.findByText("CloudOps AI");

  expect(screen.queryByRole("link", { name: /Users/ })).not.toBeInTheDocument();
  // The rest of the console is still there: this is a hidden tab, not a hidden app.
  expect(screen.getByRole("link", { name: /Alerts/ })).toBeInTheDocument();
});

it("has no chrome on the sign-in page", () => {
  pathname = "/login";
  stubApi("admin", () => undefined);

  const { container } = renderPage(<Nav />);

  expect(container).toBeEmptyDOMElement();
});
