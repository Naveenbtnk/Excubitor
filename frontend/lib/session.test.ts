import { describe, expect, it } from "vitest";
import { can, needsRole } from "@/lib/session";

/**
 * The client half of the role rule. It is cosmetic -- the API enforces the same thing -- but
 * being wrong here means offering buttons that can only produce a 403, or hiding ones that
 * would have worked.
 */
describe("can", () => {
  it("ranks viewer below operator below admin", () => {
    expect(can("viewer", "viewer")).toBe(true);
    expect(can("viewer", "operator")).toBe(false);
    expect(can("viewer", "admin")).toBe(false);

    expect(can("operator", "viewer")).toBe(true);
    expect(can("operator", "operator")).toBe(true);
    expect(can("operator", "admin")).toBe(false);

    expect(can("admin", "viewer")).toBe(true);
    expect(can("admin", "operator")).toBe(true);
    expect(can("admin", "admin")).toBe(true);
  });

  it("treats a missing or unknown role as holding nothing", () => {
    // The same fail-closed reading the API makes, so the UI can never believe it has more
    // access than the server will grant.
    expect(can(undefined, "viewer")).toBe(false);
    expect(can(null, "viewer")).toBe(false);
    expect(can("", "viewer")).toBe(false);
    expect(can("root", "viewer")).toBe(false);
    expect(can("ADMIN", "viewer")).toBe(false);
  });
});

describe("needsRole", () => {
  it("names the role that would be enough", () => {
    expect(needsRole("operator")).toBe("Requires the operator role or higher.");
    expect(needsRole("admin")).toBe("Requires the admin role or higher.");
  });
});
