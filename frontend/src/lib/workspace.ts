import { z } from "zod";
export const workspaceIdSchema = z.string().trim().min(1).max(128).regex(/^[A-Za-z0-9._-]+$/);
export type WorkspaceId = z.infer<typeof workspaceIdSchema>;
export function requireWorkspaceId(value: unknown): WorkspaceId { return workspaceIdSchema.parse(value); }
export function workspaceQueryKey(workspaceId: WorkspaceId, resource:string, ...parts:readonly unknown[]) { return ["workspace", workspaceId, resource, ...parts] as const; }
