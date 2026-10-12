// Purpose-built synthetic UI fixtures. This preview imports no API client.
export const agents = [
  { name: "综合业务助手", description: "客户、账户、贷款与工单，一处处理日常业务查询。", initial: "综", functions: 25, state: "已发布", time: "今天 10:32", color: "blue", tags: ["客户服务", "综合查询"] },
  { name: "客户经理助手", description: "快速了解客户资产、贷款与产品持有情况。", initial: "客", functions: 12, state: "已发布", time: "昨天 16:20", color: "green", tags: ["客户经营", "资金概览"] },
  { name: "账户核查助手", description: "定位账户异常，查询交易并发起核查工单。", initial: "账", functions: 8, state: "草稿", time: "昨天 14:05", color: "amber", tags: ["账户管理", "异常核查"] },
];

export type Phase = { name: string; summary: string; facts: [string, string][]; state?: string };
export type Turn = { question: string; answer: string; state: string; elapsed: string; wait: string; phases: Phase[]; balances?: boolean };
export type Conversation = { key: number; title: string; agent: string; time: string; date: string; actor: string; turns: Turn[] };
const balancePhases: Phase[] = [
  { name: "实体层", summary: "已定位客户张明，关联 3 个账户", facts: [["识别对象", "客户：张明"], ["授权候选", "1 位客户、3 个账户"], ["绑定结果", "客户与账户关系已确认"], ["绑定来源", "本轮输入与客户账户关系"]] },
  { name: "动力层", summary: "选中客户定位、账户列表和余额查询", facts: [["候选功能", "客户定位、客户资料、账户列表、账户余额"], ["选中功能", "定位客户 → 查询客户账户列表 → 查询账户余额"], ["参数校验", "通过，查询对象完整"], ["执行条件", "只读查询，当前访问范围允许"]] },
  { name: "策划层", summary: "采用「客户资金概览」，按账户汇总余额", facts: [["计划来源", "客户资金概览 · 版本 2"], ["执行顺序", "先定位客户，再查询账户，最后并行查询余额"], ["结果传递", "客户编号 → 账户清单 → 各账户余额"], ["计划校验", "通过，3 个逻辑步骤、5 次业务调用"]] },
  { name: "执行结果", summary: "3 个步骤完成，返回账户明细与资金合计", facts: [["定位客户", "输入：张明；输出：唯一客户；耗时 68 ms"], ["查询账户列表", "输出：3 个账户；耗时 94 ms"], ["查询账户余额", "并行查询 3 个账户；耗时 126 ms"], ["最终汇总", "人民币账户总余额 235,000.00 元"]] },
];
const accountListPhases: Phase[] = [
  { name: "实体层", summary: "已定位客户张明，确认客户与账户关系", facts: [["识别对象", "客户：张明"], ["授权候选", "1 位客户"], ["绑定结果", "已确认唯一客户"], ["查询范围", "该客户有权查看的账户清单"]] },
  { name: "动力层", summary: "选中客户定位和账户列表查询", facts: [["选中功能", "定位客户 → 查询客户账户列表"], ["参数校验", "客户对象已确认"], ["执行条件", "只读查询，当前访问范围允许"]] },
  { name: "策划层", summary: "先定位客户，再返回名下账户清单", facts: [["计划目标", "列出张明名下的账户"], ["执行顺序", "先定位客户，再查询账户列表"], ["结果传递", "客户编号 → 账户清单"], ["计划校验", "通过，2 个逻辑步骤、2 次业务调用"]] },
  { name: "执行结果", summary: "返回 3 个人民币账户，未查询余额", facts: [["定位客户", "输入：张明；输出：唯一客户；耗时 68 ms"], ["查询账户列表", "输出：尾号 2608、7182、9036；耗时 94 ms"], ["最终结果", "3 个人民币账户"]] },
];
const transactionFailurePhases: Phase[] = [
  { name: "实体层", summary: "已定位尾号 7182 的授权账户", facts: [["识别对象", "账户尾号：7182"], ["授权候选", "1 个账户"], ["绑定结果", "已确认目标账户"], ["绑定来源", "本轮输入中的账户尾号"]] },
  { name: "动力层", summary: "选中交易流水与详情查询", facts: [["选中功能", "查询交易流水 → 查询交易详情"], ["输入参数", "流水查询使用已绑定账户，详情查询使用流水中的交易编号"], ["执行条件", "只读查询，当前访问范围允许"]] },
  { name: "策划层", summary: "按目标账户查询最近一笔交易详情", facts: [["计划目标", "取得尾号 7182 账户最近一笔交易的详情"], ["执行顺序", "确认账户，取得最近一笔交易编号，再查询详情"], ["结果传递", "账户编号 → 最近交易编号 → 交易详情"], ["写入操作", "计划不包含写入步骤"]] },
  { name: "执行结果", summary: "交易详情查询超时，未返回业务结果", state: "失败", facts: [["出错步骤", "查询交易详情"], ["错误类别", "下游超时"], ["已完成", "账户定位、最近交易编号查询"], ["业务结果", "未取得交易详情，未执行写入"], ["后续处理", "可稍后重新查询"]] },
];
const overview: Turn = { question: "帮我看看张明名下有哪些账户，以及各账户的余额。", answer: "张明名下共有 3 个人民币账户，总余额为 235,000.00 元。各账户状态正常，明细如下。", state: "已完成", elapsed: "10.8 秒", wait: "0 秒", phases: balancePhases, balances: true };
export const conversations: Conversation[] = [
  { key: 1, title: "查询客户账户与余额", agent: "综合业务助手", time: "10:42", date: "今天", actor: "访客", turns: [
    { question: "张明名下有哪些账户？", answer: "张明名下共有 3 个人民币账户：尾号 2608、7182、9036。", state: "已完成", elapsed: "8.6 秒", wait: "0 秒", phases: accountListPhases }, overview,
  ] },
  { key: 2, title: "核查账户交易异常", agent: "综合业务助手", time: "10:25", date: "今天", actor: "李晓 · 客户经理", turns: [
    { question: "查询尾号 7182 账户最近一笔交易的详情。", answer: "本次未能取得交易详情。账户已定位，交易服务暂时不可用；没有执行任何写入操作。", state: "失败", elapsed: "5.2 秒", wait: "0 秒", phases: transactionFailurePhases },
  ] },
  { key: 3, title: "了解客户贷款情况", agent: "客户经理助手", time: "09:58", date: "今天", actor: "访客", turns: [
    { question: "帮我看一下王芳的贷款。", answer: "找到两位姓名为王芳的客户，请选择需要查询的一位。", state: "待补充", elapsed: "未结束", wait: "等待中", phases: [{ name: "实体层", summary: "发现同名客户，需要选择具体对象", facts: [["客户姓名", "王芳"], ["授权候选", "2 位"], ["当前结果", "尚未绑定查询对象"]] }, { name: "策划层", summary: "计划暂停，等待客户选择", facts: [["后续步骤", "查询贷款合同与余额"], ["业务执行", "未调用贷款查询"]] }] },
  ] },
  { key: 4, title: "统计九月账户收支", agent: "综合业务助手", time: "09:36", date: "今天", actor: "访客", turns: [{ ...overview, question: "统计尾号 2608 账户九月的收支。", answer: "本月收入 32,000.00 元，支出 12,800.00 元，净收入 19,200.00 元，共 18 笔交易。", balances: false, phases: [{ name: "执行结果", summary: "完成期间收支统计", facts: [["统计范围", "9 月 1 日至 9 月 28 日"], ["计算方式", "按已返回流水进行确定性金额统计"], ["交易笔数", "18 笔"]] }] }] },
  { key: 5, title: "查询客户账户与余额", agent: "客户经理助手", time: "16:18", date: "昨天", actor: "访客", turns: [overview] },
];
export const capabilityNames = ["定位客户", "查询账户列表", "查询账户余额", "查询账户状态", "统计期间收支", "查询贷款合同", "查询客户工单"];
