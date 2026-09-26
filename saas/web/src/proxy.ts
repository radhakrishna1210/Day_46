import { NextResponse, type NextRequest } from "next/server";

// Next 16's `proxy` (formerly middleware). A cheap first gate only: no session
// cookie -> the sign-in page. The API still verifies every request itself.
export function proxy(request: NextRequest) {
  if (!request.cookies.has("recova_session")) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = { matcher: ["/app/:path*", "/welcome"] };
