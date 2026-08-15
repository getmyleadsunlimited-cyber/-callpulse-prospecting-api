import "server-only";
import { cookies } from "next/headers";
import { SESSION_COOKIE } from "./session-config";
export async function getServerSessionToken():Promise<string|null> { return (await cookies()).get(SESSION_COOKIE)?.value ?? null; }
