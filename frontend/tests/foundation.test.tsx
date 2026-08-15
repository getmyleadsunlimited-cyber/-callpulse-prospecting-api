import { render,screen } from "@testing-library/react";
import { describe,expect,it } from "vitest";
import { AppShell } from "@/components/app-shell";
import { EmptyState,ErrorState,LoadingState } from "@/components/states";
import { mapApiError } from "@/lib/api-errors";
import { can } from "@/lib/rbac";
import { workspaceQueryKey } from "@/lib/workspace";
import { FormField } from "@/components/form-field";
describe("frontend foundation",()=>{
  it("maps API errors without exposing backend detail",()=>{expect(mapApiError(403)).toMatchObject({kind:"authorization",status:403});expect(mapApiError(500).message).toBe("An unexpected error occurred.");});
  it("isolates query cache keys by workspace",()=>{expect(workspaceQueryKey("one","leads")).not.toEqual(workspaceQueryKey("two","leads"));});
  it("models mutation capabilities by role",()=>{expect(can("viewer","mutate")).toBe(false);expect(can("member","mutate")).toBe(true);});
  it("renders an accessible shell and state primitives",()=>{render(<AppShell><EmptyState title="Ready" description="Nothing here yet"/><LoadingState/><ErrorState/></AppShell>);expect(screen.getByRole("navigation",{name:"Primary"})).toBeInTheDocument();expect(screen.getByRole("main")).toBeInTheDocument();expect(screen.getByRole("status")).toBeInTheDocument();expect(screen.getByRole("alert")).toBeInTheDocument();});
  it("associates fields with validation errors",()=>{render(<FormField name="workspace" label="Workspace" error="Required"/>);expect(screen.getByLabelText("Workspace")).toHaveAccessibleDescription("Required");});
});
