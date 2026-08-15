export const roles = ["owner","admin","member","viewer"] as const;
export type Role = (typeof roles)[number]; export type Capability = "read"|"mutate"|"manage_users"|"manage_account";
const capabilities:Record<Role,ReadonlySet<Capability>> = { owner:new Set(["read","mutate","manage_users","manage_account"]), admin:new Set(["read","mutate"]), member:new Set(["read","mutate"]), viewer:new Set(["read"]) };
export function can(role:Role, capability:Capability):boolean { return capabilities[role].has(capability); }
