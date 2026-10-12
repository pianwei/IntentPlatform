import type { FunctionDefinition } from "../../shared/types";

export const skillDocumentTemplates = [
  { id: "overview", name: "查询汇总", description: "查询多项资料，汇总结果并说明缺项。" },
  { id: "comparison", name: "对比分析", description: "按相同口径对比对象，保留资料来源。" },
  { id: "draft", name: "资料草稿", description: "检索所需资料，形成报告或纪要草稿。" },
  { id: "confirmed-write", name: "确认办理", description: "先查询核对，再确认并办理一次业务写入。" },
] as const;

export type SkillTemplateId = typeof skillDocumentTemplates[number]["id"];

export function makeSkillDocumentTemplate(id: SkillTemplateId, definition: FunctionDefinition, childNames: string[]) {
  const goal = definition.description.trim() || "[填写适用的业务目标，以及不适用的情况]";
  const fields = Object.keys(definition.input_schema.properties ?? {});
  const inputs = fields.length ? fields.map(name => `「${name}」`).join("、") : "[填写所需信息；无必填参数时说明查询范围]";
  const children = childNames.length ? childNames.map(name => `- ${name}`).join("\n") : "- [先添加已发布子功能，再补充执行顺序]";
  const rules: Record<SkillTemplateId, string> = {
    overview: "1. 明确查询对象与范围；对象有歧义时先询问。\n2. 从下列子功能选择目标所需的查询；独立查询可并行，有依赖的查询按结果先后执行。\n3. 参数只取用户输入或已完成查询的获准结果，并按子功能实际字段填写。\n4. 汇总已完成的结果，列出资料缺项与未完成事项，不补造业务事实。",
    comparison: "1. 明确需要比较的对象、维度与时间范围；信息不足时先询问。\n2. 使用下列子功能查询各对象资料，保持同一统计或描述口径。\n3. 只对实际返回的共同维度进行比较；不一致或缺失的数据单独说明。\n4. 保留资料来源，不把模型分析当成已查询的业务事实。",
    draft: "1. 明确草稿类型、对象与使用目的；信息不足时先询问。\n2. 使用下列子功能收集必要资料，按依赖传递获准结果。\n3. 根据已查询资料组织草稿，未取得的材料标记为待补充。\n4. 草稿仅供查看与复核，不自动提交或录入业务系统。",
    "confirmed-write": "1. 使用下列查询子功能核对目标对象与办理条件。\n2. 只在资料和条件满足时准备一个末尾写操作；目标、参数或计划改变后重新确认。\n3. 向用户展示已解析的对象与写入参数，取得确认后才执行。\n4. 用户拒绝则停止；结果未知时进入核对，不自动重发写入。",
  };
  const answer = id === "comparison" ? "先给出比较结论，再用 Markdown 表格列出共同维度、差异及来源。" : id === "draft" ? "用 Markdown 返回可复核的草稿，分列资料来源与待补充材料。" : id === "confirmed-write" ? "用 Markdown 说明办理结果与后续事项，保留成功、失败或未知的真实状态。" : "先给出结论，再用 Markdown 分项列出查询结果、来源及缺失资料。";
  return `## 适用目标\n${definition.name.trim() || "[填写组合功能名称]"}\n${goal}\n\n## 所需输入\n${inputs}\n缺少必要信息时先补参；仅使用当前授权范围内的数据。\n\n## 可用子功能\n${children}\n\n## 办理规则\n${rules[id]}\n\n## 回答要求\n${answer}\n仅使用已完成子功能的获准结果；必要步骤失败时停止依赖步骤，并说明未完成部分。`;
}
