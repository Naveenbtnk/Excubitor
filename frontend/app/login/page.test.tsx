import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import LoginPage from "./page";
import { isDevBuild } from "@/lib/env";
import { jsonResponse, renderPage } from "@/test/support";

/**
 * The sign-in page is the one screen reachable without a session and the only place the session
 * cookie is created, so these tests cover both halves of it: that the front door looks like a
 * product (brand panel, revealed password, an alert when it goes wrong) and that the request it
 * sends is still exactly the one it used to send -- same endpoint, same cookie, same `next`.
 *
 * The seeded-administrator hint is the one thing here that depends on which kind of build is
 * running, so it is tested in both states: the variables are named while developing, and a
 * production build renders the page without them.
 */

const push = vi.fn();
const refresh = vi.fn();
let search = "";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
  useSearchParams: () => new URLSearchParams(search),
}));

interface RecordedCall {
  url: string;
  method: string;
  body: string | undefined;
  credentials: RequestCredentials | undefined;
}

/** Stub `fetch` for the sign-in POST, recording the whole request, not just the path. */
function stubLogin(route: (url: string, method: string) => Response | undefined) {
  const calls: RecordedCall[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({
        url,
        method,
        body: typeof init?.body === "string" ? init.body : undefined,
        credentials: init?.credentials,
      });
      const response = route(url, method);
      if (response) return response;
      // Every test asks about the demo login on mount, whether it cares or not. The default is
      // "this deployment has none", which is also the honest default for a test.
      if (url.includes("/auth/demo")) {
        return jsonResponse({ enabled: false, email: null, password: null });
      }
      return jsonResponse({ detail: `Unstubbed request: ${method} ${url}` }, 404);
    })
  );

  return calls;
}

function fillIn(email = "ops@excubitor.dev", password = "correct-horse") {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}

const signIn = () => screen.getByRole("button", { name: /sign in$/i });

const accepted = (url: string) => (url.includes("/auth/login") ? jsonResponse({ ok: true }) : undefined);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  push.mockClear();
  refresh.mockClear();
  search = "";
});

it("opens on a brand panel: the mark, the name and the watch tagline", async () => {
  stubLogin(() => undefined);
  const { container } = renderPage(<LoginPage />);
  // The page asks about a shared demo login as it mounts. Letting that answer land inside `act`
  // keeps a request that is still in flight from becoming a state update after the test.
  await act(async () => {});

  expect(screen.getByRole("heading", { level: 1, name: "Excubitor" })).toBeInTheDocument();
  expect(screen.getByText(/Keeps watch so you/)).toBeInTheDocument();
  // The panel and the mobile header each draw the mark.
  expect(container.querySelectorAll("svg").length).toBeGreaterThanOrEqual(2);
  // The halo behind the mark, which is the whole of the animation.
  expect(container.querySelector(".watch-rings")).not.toBeNull();
});

it("starts empty rather than pre-filling the seeded account", async () => {
  stubLogin(() => undefined);
  renderPage(<LoginPage />);
  await act(async () => {});

  expect(screen.getByLabelText("Email")).toHaveValue("");
  expect(screen.getByLabelText("Password")).toHaveValue("");
});

it("posts the typed credentials, with the cookie, and follows `next`", async () => {
  search = "?next=/alerts";
  const calls = stubLogin(accepted);
  renderPage(<LoginPage />);

  fillIn();
  fireEvent.click(signIn());

  await waitFor(() => expect(push).toHaveBeenCalledWith("/alerts"));
  const posted = calls.find((call) => call.url.includes("/auth/login"));
  expect(posted?.method).toBe("POST");
  expect(posted?.body).toBe(JSON.stringify({ email: "ops@excubitor.dev", password: "correct-horse" }));
  expect(posted?.credentials).toBe("include");
});

it("has no `next` destination and still lands on the console", async () => {
  const calls = stubLogin(accepted);
  renderPage(<LoginPage />);

  fillIn();
  fireEvent.click(signIn());

  await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  expect(calls.filter((call) => call.url.includes("/auth/login"))).toHaveLength(1);
});

it("signs in from the keyboard: the control is the form's own submit button", async () => {
  stubLogin(accepted);
  const { container } = renderPage(<LoginPage />);

  const button = signIn();
  expect(button).toHaveAttribute("type", "submit");
  expect(container.querySelector("form")).toContainElement(button);

  fillIn("keyboard@excubitor.dev", "tab-enter");
  fireEvent.submit(container.querySelector("form") as HTMLFormElement);

  await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
});

it("disables the control and says it is working while the request is open", async () => {
  let release!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>((resolve) => (release = resolve)))
  );
  renderPage(<LoginPage />);

  fillIn();
  const button = signIn();
  fireEvent.click(button);

  await waitFor(() => expect(button).toBeDisabled());
  expect(button).toHaveAttribute("aria-busy", "true");
  expect(button).toHaveTextContent("Signing in");

  release(jsonResponse({ ok: true }));
  await waitFor(() => expect(button).toBeEnabled());
});

it("shows a refused sign-in as an alert and stays on the page", async () => {
  stubLogin((url) =>
    url.includes("/auth/login")
      ? jsonResponse({ detail: "Incorrect email or password." }, 401)
      : undefined
  );
  renderPage(<LoginPage />);

  fillIn();
  fireEvent.click(signIn());

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Incorrect email or password.");
  expect(push).not.toHaveBeenCalled();
  expect(signIn()).toBeEnabled();
});

it("turns a 422 body into the sentence the API wrote", async () => {
  stubLogin((url) =>
    url.includes("/auth/login")
      ? jsonResponse({ detail: [{ msg: "Input should be a valid email address" }] }, 422)
      : undefined
  );
  const { container } = renderPage(<LoginPage />);

  // Submitted past the browser's own validation on purpose: that validation is a convenience,
  // and the API's answer is what the page has to render when it is the one that refuses.
  fillIn("not-an-email");
  fireEvent.submit(container.querySelector("form") as HTMLFormElement);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Input should be a valid email address"
  );
});

it("reveals the password only when asked", async () => {
  stubLogin(() => undefined);
  renderPage(<LoginPage />);
  await act(async () => {});

  const password = screen.getByLabelText("Password");
  expect(password).toHaveAttribute("type", "password");

  const show = screen.getByRole("button", { name: "Show password" });
  expect(show).toHaveAttribute("aria-pressed", "false");

  fillIn();
  fireEvent.click(show);

  expect(password).toHaveAttribute("type", "text");
  expect(screen.getByRole("button", { name: "Hide password" })).toHaveAttribute(
    "aria-pressed",
    "true"
  );
  // The same field, so the value typed before the switch is still there.
  expect(password).toHaveValue("correct-horse");
});

it("points a developer at the seeded administrator without signing them in", async () => {
  stubLogin(() => undefined);
  renderPage(<LoginPage />);
  await act(async () => {});

  expect(screen.getByText("ADMIN_EMAIL")).toBeInTheDocument();
  expect(screen.getByText("ADMIN_PASSWORD")).toBeInTheDocument();
});

it("renders for production without the deployment's own instructions", async () => {
  vi.stubEnv("NODE_ENV", "production");
  stubLogin(() => undefined);
  renderPage(<LoginPage />);
  await act(async () => {});

  // The hint is the only place either variable is named, so its absence is the assertion.
  expect(screen.queryByText("ADMIN_EMAIL")).not.toBeInTheDocument();
  expect(screen.queryByText("ADMIN_PASSWORD")).not.toBeInTheDocument();
  expect(document.body.textContent).not.toContain("seeded administrator");
  // Everything a stranger needs is still there.
  expect(screen.getByRole("heading", { level: 2, name: "Sign in" })).toBeInTheDocument();
  expect(screen.getByLabelText("Email")).toBeInTheDocument();
  expect(signIn()).toBeEnabled();
});

it("offers no demo button when the deployment has no shared login", async () => {
  const calls = stubLogin(() => undefined);
  renderPage(<LoginPage />);

  // Wait for the answer itself, not for the assertion that depends on it: SWR settling is a
  // state update, and asserting while it is still in flight would decide this test by timing.
  await waitFor(() => expect(calls.some((call) => call.url.includes("/auth/demo"))).toBe(true));
  await act(async () => {});

  expect(screen.queryByRole("button", { name: /try the demo/i })).toBeNull();
  // The form is still the whole front door.
  expect(signIn()).toBeEnabled();
});

it("offers the shared login, and fills the form with it when asked", async () => {
  stubLogin((url) =>
    url.includes("/auth/demo")
      ? jsonResponse({
          enabled: true,
          email: "demo@excubitor.local",
          password: "published-on-purpose",
        })
      : undefined
  );
  renderPage(<LoginPage />);

  const tryDemo = await screen.findByRole("button", { name: /try the demo/i });
  // Prefilling is all it does: nothing is sent until the visitor presses Sign in themselves.
  expect(screen.getByLabelText("Email")).toHaveValue("");

  fireEvent.click(tryDemo);

  expect(screen.getByLabelText("Email")).toHaveValue("demo@excubitor.local");
  expect(screen.getByLabelText("Password")).toHaveValue("published-on-purpose");
  expect(push).not.toHaveBeenCalled();
});

it("answers the build-mode question the way Next substitutes it", () => {
  expect(isDevBuild("development")).toBe(true);
  expect(isDevBuild("test")).toBe(true);
  expect(isDevBuild(undefined)).toBe(true);
  expect(isDevBuild("production")).toBe(false);
});
