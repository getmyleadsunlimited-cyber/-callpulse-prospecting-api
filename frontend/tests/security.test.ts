// @vitest-environment node
import { describe,expect,it } from "vitest";
import { assertCsrfToken,assertMutationOrigin } from "@/lib/csrf";
import { SESSION_COOKIE,sessionCookieOptions } from "@/lib/session-config";
describe("BFF security",()=>{
  it("keeps the session token in an HttpOnly Secure cookie",()=>{expect(SESSION_COOKIE.startsWith("__Host-")).toBe(true);expect(sessionCookieOptions.httpOnly).toBe(true);expect(sessionCookieOptions.secure).toBe(true);expect(sessionCookieOptions.sameSite).toBe("lax");});
  it("requires the configured same origin",()=>{const valid=new Request("https://app.example/api",{headers:{origin:"https://app.example","sec-fetch-site":"same-origin"}});expect(()=>assertMutationOrigin(valid,"https://app.example")).not.toThrow();const invalid=new Request("https://app.example/api",{headers:{origin:"https://evil.example"}});expect(()=>assertMutationOrigin(invalid,"https://app.example")).toThrow();});
  it("requires matching CSRF cookie and header tokens",()=>{const valid=new Request("https://app.example/api",{headers:{"x-csrf-token":"abc"}});expect(()=>assertCsrfToken(valid,"abc")).not.toThrow();expect(()=>assertCsrfToken(valid,"def")).toThrow();});
});
