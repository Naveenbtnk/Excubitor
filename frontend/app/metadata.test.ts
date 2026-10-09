/**
 * The document titles, which nothing else in the suite can see.
 *
 * The brand string is the part that got renamed, and it is the part that is easiest to leave
 * behind: a route title is a one-line export in a file no page test ever imports. So this test
 * walks the route layouts, applies the root template the way Next does, and asserts on the string
 * a browser tab would actually show.
 */

import { expect, it } from "vitest";
import { metadata as rootMetadata } from "@/app/layout";
import { metadata as alerts } from "@/app/alerts/layout";
import { metadata as incident } from "@/app/incidents/[id]/layout";
import { metadata as knowledge } from "@/app/knowledge/layout";
import { metadata as login } from "@/app/login/layout";
import { metadata as postMortems } from "@/app/post-mortems/layout";
import { metadata as sandbox } from "@/app/sandbox/layout";
import { metadata as adminUsers } from "@/app/admin/users/layout";

const routes = [alerts, knowledge, postMortems, sandbox, adminUsers, incident, login];

/** What Next does with a child title and the parent template. */
function tabTitle(route: { title?: unknown }) {
  const template = (rootMetadata.title as { template: string }).template;
  return template.replace("%s", String(route.title));
}

it("names the product in the title of the console itself", () => {
  expect((rootMetadata.title as { default: string }).default).toBe("Excubitor — CloudOps AI");
});

it("keeps the brand first in every route title", () => {
  for (const route of routes) {
    expect(tabTitle(route)).toMatch(/^Excubitor — \S/);
  }
});

it("describes itself with the brand and the descriptor", () => {
  expect(rootMetadata.description).toContain("Excubitor");
  expect(rootMetadata.description).toContain("CloudOps AI");
});
