import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiClient } from "../shared/api";
import { BusinessCatalogPage } from "../console/src/BusinessCatalogPage";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows the published object-to-action-to-function tree and opens the selected function", async () => {
  const api = new ApiClient("management");
  const open = vi.fn();
  const relations = { objects: [{ id: "business:Customer", name: "客户", actions: [{ key: "READ", name: "查询", functions: [
    { id: "fn-customer", name: "查询客户资料", version: 2, execution_kind: "direct", composites: [
      { id: "fn-overview", name: "客户概览", version: 1 },
    ] },
  ] }] }] };
  vi.spyOn(api, "get").mockImplementation(async path => path.includes("/relationships?") ? relations as never : { items: [], defaults: [] } as never);
  render(<BusinessCatalogPage api={api} workspace="ws" writable onOpenFunction={open} />);

  fireEvent.click(screen.getByRole("tab", { name: "功能关系" }));
  await waitFor(() => expect(api.get).toHaveBeenCalledWith("/api/v2/management/workspaces/ws/business-catalog/relationships?include_drafts=false"));
  fireEvent.click(screen.getByText("客户"));
  expect(await screen.findByRole("heading", { name: "查询" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "查询客户资料" }));
  expect(open).toHaveBeenCalledWith("fn-customer");
  expect(screen.getByRole("button", { name: "客户概览 · v1" })).toBeVisible();
});
