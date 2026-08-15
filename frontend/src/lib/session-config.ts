export const SESSION_COOKIE = "__Host-callpulse_session";
// The __Host- prefix requires Secure, Path=/, and no Domain attribute.
export const sessionCookieOptions = { httpOnly:true, secure:true, sameSite:"lax" as const, path:"/" };
