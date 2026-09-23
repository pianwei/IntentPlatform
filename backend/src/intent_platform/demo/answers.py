"""Plain-language answers from approved synthetic results, without model claims."""
from decimal import Decimal, InvalidOperation


def _list(value, field):
    rows = value.get(field, []) if isinstance(value, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _money(value):
    try:
        return f"{Decimal(str(value)):,.2f}"
    except (InvalidOperation, TypeError):
        return "未记录"


def render_demo_answer(skill_key, rows, inputs):
    """Only the already projected result rows may contribute to the answer."""
    data = {row["step"]: row["data"] for row in rows}
    if skill_key == "demo.skill.customer_funds":
        customers = _list(data.get("customer"), "customers")
        accounts = _list(data.get("accounts"), "accounts")
        balances = {row.get("account_id"): row for row in data.get("balances", [])
                    if isinstance(row, dict)}
        customer = customers[0] if customers else {}
        heading = f"{customer.get('name', '该客户')}（{customer.get('customer_id', '编号未记录')}）共有 {len(accounts)} 个账户。"
        details = []
        totals = {}
        for account in accounts:
            balance = balances.get(account.get("account_id"))
            if not balance:
                details.append(f"{account.get('account_id', '账户')}余额未返回")
                continue
            currency = balance.get("currency", account.get("currency", ""))
            details.append(f"{account.get('account_id', '账户')}（{account.get('label', '账户')}）余额 {_money(balance.get('balance'))} {currency}，可用 {_money(balance.get('available'))} {currency}")
            try:
                totals[currency] = totals.get(currency, Decimal(0)) + Decimal(str(balance["balance"]))
            except (KeyError, InvalidOperation, TypeError):
                pass
        total_text = "；".join(f"{_money(amount)} {currency}" for currency, amount in sorted(totals.items()))
        return heading + ("\n" + "；".join(details) + "。" if details else "") + (f"\n已返回余额合计：{total_text}。" if total_text else "")
    if skill_key == "demo.skill.account_cashflow":
        summary = data.get("summary")
        if isinstance(summary, dict):
            return (f"账户 {summary.get('account_id', inputs.get('account_id', ''))} 在 "
                    f"{summary.get('start_date', '')} 至 {summary.get('end_date', '')} 共 {summary.get('count', 0)} 笔流水。"
                    f"收入 {_money(summary.get('income'))} 元，支出 {_money(summary.get('expense'))} 元，"
                    f"净额 {_money(summary.get('net'))} 元。金额由业务统计处理器计算。")
    if skill_key == "demo.skill.customer_loans":
        customers = _list(data.get("customer"), "customers")
        loans = _list(data.get("loans"), "loans")
        customer = customers[0] if customers else {}
        balances = {row.get("loan_id"): row for row in data.get("balances", []) if isinstance(row, dict)}
        schedules = [item for batch in data.get("schedules", []) if isinstance(batch, dict)
                     for item in _list(batch, "payments")]
        overdue = [item for batch in data.get("overdue", []) if isinstance(batch, dict)
                   for item in _list(batch, "overdue")]
        details = [f"{loan.get('loan_id', '合同')}（{loan.get('product_name', '贷款')}）剩余余额 "
                   f"{_money(balances.get(loan.get('loan_id'), {}).get('outstanding'))} {loan.get('currency', '')}，"
                   f"还款安排 {sum(item.get('loan_id') == loan.get('loan_id') for item in schedules)} 项，"
                   f"逾期记录 {sum(item.get('loan_id') == loan.get('loan_id') for item in overdue)} 项"
                   for loan in loans]
        return (f"{customer.get('name', '该客户')}（{customer.get('customer_id', '编号未记录')}）有 {len(loans)} 份贷款合同。"
                + ("\n" + "；".join(details) + "。" if details else ""))
    if skill_key == "demo.skill.abnormal_account":
        status = data.get("status")
        ticket = data.get("ticket")
        if isinstance(status, dict):
            line = f"账户 {status.get('account_id', inputs.get('account_id', ''))} 当前状态为 {status.get('status', '未记录')}。"
            if isinstance(ticket, dict):
                return line + f"已按确认创建模拟核查工单 {ticket.get('ticket_id', '编号未记录')}。"
            return line + "当前条件未触发核查工单创建。"
    if skill_key == "demo.skill.customer_panorama":
        detail = data.get("detail")
        if isinstance(detail, dict):
            return (f"{detail.get('name', '该客户')}（{detail.get('customer_id', '编号未记录')}，"
                    f"{detail.get('segment', '类别未记录')}）现有 "
                    f"{len(_list(data.get('accounts'), 'accounts'))} 个账户、"
                    f"{len(_list(data.get('loans'), 'loans'))} 份贷款合同、"
                    f"{len(_list(data.get('holdings'), 'holdings'))} 项持有产品。详情见上方业务结果。")
    return "已完成获准的业务步骤。详细结果见上方业务卡片。"
