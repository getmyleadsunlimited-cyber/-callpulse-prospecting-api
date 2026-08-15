import "server-only";
import { getServerSessionToken } from "./session";
import { mapApiError } from "./api-errors";
function apiBaseUrl():string { const value=process.env.CALLPULSE_API_URL; if (!value) throw new Error("CALLPULSE_API_URL is required on the server"); return value.replace(/\/$/,""); }
export async function serverApi<T>(path:string,init:RequestInit={}):Promise<T> { const token=await getServerSessionToken(); const headers=new Headers(init.headers); headers.set("accept","application/json"); if(init.body) headers.set("content-type","application/json"); if(token) headers.set("authorization",`Bearer ${token}`); const response=await fetch(`${apiBaseUrl()}${path}`,{...init,headers,cache:"no-store"}); if(!response.ok) throw mapApiError(response.status); return response.json() as Promise<T>; }
