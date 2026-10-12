import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { DesignPreview } from "../preview/src/App";

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
});
beforeEach(() => {
  history.replaceState(null, "", "/console/design-preview/#agents");
  vi.stubGlobal("fetch", vi.fn(() => { throw new Error("Preview must not make API requests"); }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function nav(label: string) { fireEvent.click(within(screen.getByRole("navigation", { name: "主导航" })).getByRole("button", { name: new RegExp(label) })); }
function createFunction() { nav("功能库"); fireEvent.click(screen.getByRole("button", { name: "新建功能" })); }

describe("isolated interactive preview", () => {
  it("filters agents without technical identifiers or API calls", () => {
    render(<DesignPreview />);
    fireEvent.change(screen.getByRole("textbox", { name: "搜索智能体" }), { target: { value: "客户经理" } });
    expect(screen.getByRole("button", { name: "客户经理助手" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "综合业务助手" })).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("WORKSPACE");
    expect(document.body.textContent).not.toMatch(/agent_[0-9a-f]|run_[0-9a-f]|admin ·/);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("groups conversation turns and changes final answers without opening another session", () => {
    render(<DesignPreview />); nav("运行日志");
    expect(screen.getByText("各账户状态正常，明细如下。", { exact: false })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "第 1 轮" }));
    expect(screen.getByText("张明名下共有 3 个人民币账户：尾号 2608、7182、9036。")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /查询客户账户与余额/ })).toHaveLength(2);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("filters failed conversations and exposes the failed phase", () => {
    render(<DesignPreview />); nav("运行日志");
    fireEvent.change(screen.getByRole("combobox", { name: "筛选状态" }), { target: { value: "失败" } });
    expect(screen.getByText("1 个会话")).toBeInTheDocument();
    expect(screen.getByText("交易详情查询超时，未返回业务结果").closest("details")).toHaveAttribute("open");
    expect(screen.getByText("本次未能取得交易详情。", { exact: false })).toBeInTheDocument();
  });
  it("opens the processing stages from the fixed detail toolbar without scrolling past the answer", () => {
    render(<DesignPreview />); nav("运行日志");
    fireEvent.click(screen.getByRole("button", { name: "查看处理过程" }));
    expect(screen.queryByText("用户输入")).not.toBeInTheDocument();
    expect(screen.getByText("实体层")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回问答" }));
    expect(screen.getByText("用户输入")).toBeInTheDocument();
  });
  it("shows an empty filter result and restores the list", () => {
    render(<DesignPreview />); nav("运行日志");
    fireEvent.click(screen.getAllByRole("button", { name: /查询客户账户与余额/ })[0]);
    expect(document.querySelector(".log-workbench")).toHaveClass("show-detail");
    fireEvent.change(screen.getByRole("textbox", { name: "搜索会话" }), { target: { value: "不存在的会话" } });
    expect(screen.getByText("没有匹配的会话")).toBeInTheDocument();
    expect(document.querySelector(".log-workbench")).not.toHaveClass("show-detail");
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(screen.getByText("5 个会话")).toBeInTheDocument();
  });
  it("blocks incomplete steps and supports a simulated validation and publication", () => {
    render(<DesignPreview />); createFunction();
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByText("请填写功能名称，例如：查询账户余额")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "填入示例" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByRole("textbox", { name: "参数名称 1" })).toHaveValue("account_id");
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByRole("button", { name: "预览发布" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "运行示例" }));
    fireEvent.click(screen.getByRole("button", { name: "预览发布" }));
    expect(screen.getByText("发布流程预览完成")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("validates visible parameter rows and retains edits between steps", () => {
    render(<DesignPreview />); createFunction();
    fireEvent.click(screen.getByRole("button", { name: "填入示例" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "添加参数" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByText("请补全参数名称")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "参数名称 2" }), { target: { value: "account_id" } });
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByText("参数名称不能重复")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "参数名称 2" }), { target: { value: "currency" } });
    fireEvent.click(screen.getByRole("button", { name: "上一步" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByRole("textbox", { name: "参数名称 2" })).toHaveValue("currency");
  });
  it("asks before discarding unsaved form edits", () => {
    render(<DesignPreview />); createFunction();
    fireEvent.click(screen.getByRole("button", { name: "填入示例" }));
    nav("运行日志");
    const dialog = screen.getByRole("dialog", { name: "保留当前修改？" });
    fireEvent.click(within(dialog).getByRole("button", { name: "继续编辑" }));
    expect(screen.getByRole("heading", { name: "新建功能" })).toBeInTheDocument();
    nav("运行日志");
    fireEvent.click(screen.getByRole("button", { name: "放弃修改并离开" }));
    expect(screen.getByRole("heading", { name: "运行日志" })).toBeInTheDocument();
  });
  it("preserves saved draft fields when leaving and reopening, including conditional controls", () => {
    render(<DesignPreview />); createFunction();
    fireEvent.click(screen.getByRole("button", { name: "填入示例" }));
    fireEvent.change(screen.getByRole("combobox", { name: /接入类型/ }), { target: { value: "HTTP 接口" } });
    fireEvent.change(screen.getByRole("combobox", { name: "请求方法" }), { target: { value: "POST" } });
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.change(screen.getByRole("combobox", { name: "展示形式" }), { target: { value: "表格" } });
    fireEvent.click(screen.getByRole("button", { name: "上一步" }));
    expect(screen.getByRole("combobox", { name: "请求方法" })).toHaveValue("POST");
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    nav("运行日志"); createFunction();
    expect(screen.getByRole("combobox", { name: "请求方法" })).toHaveValue("POST");
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    expect(screen.getByRole("combobox", { name: "展示形式" })).toHaveValue("表格");
    fireEvent.change(screen.getByRole("combobox", { name: "参数来源" }), { target: { value: "仅用户输入" } });
    nav("智能体");
    expect(screen.getByRole("dialog", { name: "保留当前修改？" })).toBeInTheDocument();
  });
  it("never opens a different function when selecting a non-editable preview row", () => {
    render(<DesignPreview />); nav("功能库");
    const row = screen.getByText("查询贷款合同").closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "查看" }));
    expect(screen.getByRole("dialog", { name: "查询贷款合同" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "编辑功能" })).not.toBeInTheDocument();
  });
  it("opens account settings, changes density, and never creates real credentials", () => {
    render(<DesignPreview />);
    fireEvent.click(screen.getByRole("button", { name: "打开用户菜单" }));
    fireEvent.click(screen.getByRole("button", { name: "个人中心" }));
    const dialog = screen.getByRole("dialog", { name: "个人中心" });
    fireEvent.click(within(dialog).getByRole("button", { name: "偏好设置" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "紧凑" }));
    expect(document.querySelector(".preview-app")).toHaveClass("compact");
    fireEvent.click(within(dialog).getByRole("button", { name: "API Key" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "创建 Key" }));
    expect(screen.getByText("此预览不生成可用凭据。", { exact: false })).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
});
