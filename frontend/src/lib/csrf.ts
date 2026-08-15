import { timingSafeEqual } from "node:crypto";
import { CSRF_HEADER } from "./csrf-constants";
function safeEqual(left:string,right:string):boolean { const a=Buffer.from(left); const b=Buffer.from(right); return a.length===b.length && timingSafeEqual(a,b); }
export function assertMutationOrigin(request:Request, expectedOrigin:string):void { const origin=request.headers.get("origin"); if (!origin || origin!==expectedOrigin) throw new Error("Invalid request origin"); const fetchSite=request.headers.get("sec-fetch-site"); if (fetchSite && fetchSite!=="same-origin") throw new Error("Cross-site mutation rejected"); }
export function assertCsrfToken(request:Request,cookieToken?:string):void { const headerToken=request.headers.get(CSRF_HEADER); if (!cookieToken || !headerToken || !safeEqual(cookieToken,headerToken)) throw new Error("Invalid CSRF token"); }
