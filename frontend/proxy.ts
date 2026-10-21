import { NextResponse, type NextRequest } from "next/server";

const protectedRoutes = [
  "/",
  "/admin/users",
  "/alerts",
  "/incidents",
  "/knowledge",
  "/post-mortems",
  "/sandbox",
];

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const session = request.cookies.get("cloudops_session");

  if (pathname === "/login" && session) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  const protectedRoute = protectedRoutes.some(
    (route) => pathname === route || (route !== "/" && pathname.startsWith(`${route}/`))
  );

  if (protectedRoute && !session) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
