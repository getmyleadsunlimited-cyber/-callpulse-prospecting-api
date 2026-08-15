import AxeBuilder from "@axe-core/playwright";
import { expect,test } from "@playwright/test";
test("shell has no automatically detectable accessibility violations",async({page})=>{await page.goto("/");await expect(page.getByRole("main")).toBeVisible();expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);});
