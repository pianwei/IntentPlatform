from intent_platform.business_markdown import covers_rows, render_rows


def test_only_full_deterministic_rendering_covers_the_approved_rows():
    rows = [{"name": "获准结果", "data": {"count": 7, "source": "模拟资料"}}]
    answer = render_rows(rows)
    assert covers_rows(answer, rows, [])
    assert not covers_rows("共有7条记录。", rows, [])
    assert not covers_rows(answer, [{"name": "获准结果", "data": {"count": 8}}], [])


def test_truncation_and_markdown_table_limits_keep_supplementary_results():
    rows = [{"name": "长结果", "data": {"text": "x" * 16000}}]
    assert not covers_rows(render_rows(rows), rows, [])
    assert not covers_rows(render_rows(rows, limit=None), rows, [])
    wide = [{"name": "宽表", "data": [{str(i): i for i in range(21)}]}]
    assert not covers_rows(render_rows(wide), wide, [])
    long = [{"name": "旧记录", "data": list(range(101))}]
    assert not covers_rows(render_rows(long), long, [])


def test_no_output_receipt_contains_no_raw_business_payload():
    answer = render_rows([{"name": "发送通知", "data": {}}], [{"name": "发送通知", "output_mode": "none"}])
    assert "业务执行已完成，无业务数据返回" in answer
