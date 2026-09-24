# -*- coding: utf-8 -*-
"""禅道 MCP 服务的回归测试套件（标准库 unittest，零外部依赖）。

覆盖两类东西：
  1. **纯函数**：正文清洗 / HTML 转义 / 箭头规范 / 类型与严重程度解析
  2. **客户端行为**（用假 Session，不打真实禅道）：401 重入与 Token 刷新、
     重试策略、分页去重与闸门、需求解析、用户解析、建单兜底、web 假成功判定

每个用例都对应一个**实测过的真实缺陷**，注释里写明"旧行为为什么会错"，
以免后人"优化"时又把坑挖回来。

运行（无需 pytest，用标准库即可）：
    .venv\\Scripts\\python.exe zentao_mcp\\tests\\test_zentao_mcp.py
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import requests

SRC = Path(__file__).resolve().parents[1] / "zentao_mcp_server.py"
_spec = importlib.util.spec_from_file_location("zentao_mcp_server", SRC)
z = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(z)


# ─────────────────────────── 测试替身 ───────────────────────────

class FakeResp:
    def __init__(self, status_code=200, json_data=None, text=None):
        self.status_code = status_code
        self._json = json_data
        if text is not None:
            self.text = text
        elif json_data is not None:
            self.text = json.dumps(json_data, ensure_ascii=False)
        else:
            self.text = ""

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


class FakeSession:
    """把请求交给 handler(method, url, kw, n) 决定响应，并记录全部调用。"""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.headers = {}

    def request(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, "kw": kw})
        return self.handler(method, url, kw, len(self.calls))

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


def make_client(handler, account="acc", password="pwd"):
    c = z.ZentaoClient("http://zt.local/zentao", account, password)
    c.s = FakeSession(handler)
    return c


def token_of(call):
    return (call["kw"].get("headers") or {}).get("Token")


# ─────────────────── 1. 纯函数：正文与 HTML 处理 ───────────────────

class TestSanitizePathArrows(unittest.TestCase):
    def test_tag_boundary_is_never_touched(self):
        """旧实现会把标签间的空格当箭头：</b> <code> → </b> → <code>（实测复现）。"""
        src = "<p>列表 <b>排序</b> <code>sort</code></p>"
        self.assertEqual(z._sanitize_path_arrows(src), src)

    def test_pre_and_code_are_verbatim(self):
        """pre/code 内是代码原文，=> / -> 不能被改成 =→。"""
        src = "<pre>var g = x => String(x)\nWHERE a > b</pre>"
        self.assertEqual(z._sanitize_path_arrows(src), src)
        self.assertEqual(z._sanitize_path_arrows("<code>a -> b</code>"), "<code>a -> b</code>")

    def test_operators_outside_tags_are_protected(self):
        src = "<p>箭头函数 x => y 与指针 p -> q</p>"
        self.assertEqual(z._sanitize_path_arrows(src), src)

    def test_bare_lt_is_not_a_tag(self):
        """旧实现把 `<[^>]*>` 当标签，"数量<5 时点 保存 > 提交" 里的裸 > 会被静默漏改。"""
        self.assertEqual(
            z._sanitize_path_arrows("<p>数量<5 时点 保存 > 提交</p>"),
            "<p>数量<5 时点 保存 → 提交</p>",
        )

    def test_menu_paths_are_normalized(self):
        self.assertEqual(z._sanitize_path_arrows("<p>审批流管理 > 审批流模板管理</p>"),
                         "<p>审批流管理 → 审批流模板管理</p>")
        for src in ("列表 > 排序", "列表 >排序", "列表> 排序"):
            self.assertEqual(z._sanitize_path_arrows(src), "列表 → 排序")

    def test_empty(self):
        self.assertEqual(z._sanitize_path_arrows(""), "")


class TestStepsHtml(unittest.TestCase):
    def test_interface_url_is_escaped(self):
        """URL 未转义就拼进单引号属性：含 ' 即断开属性并可注入。"""
        out = z._steps_html("1. 步骤", interface_url="http://x/a'b")
        self.assertIn("&#x27;", out)
        self.assertNotIn("href='http://x/a'b'", out)

    def test_steps_with_marker_passes_through(self):
        out = z._steps_html("<h3>[步骤]</h3><p>1. x</p>")
        self.assertIn("<h3>[步骤]</h3><p>1. x</p>", out)
        self.assertNotIn("<p>[步骤]</p><p><h3>", out)

    def test_multiline_pre_is_kept_intact(self):
        """旧实现逐行包 <p>，多行 <pre> 被拆成一段段 <p>（实测 #65916 三个代码块碎成 40+ 个 <p>）。"""
        code = "<pre>r = e.table.getRecordByCell(e.col, e.row)\n    if (o) {\n        b(u);\n    }</pre>"
        out = z._steps_html("1. 打开编辑态\n2. 双击行\n" + code)
        self.assertIn(code, out)                  # 整块、含缩进，一字不差
        self.assertNotIn("<pre>r = e.table", out.replace(code, ""))
        self.assertNotIn("</p><p>if (o) {", out)

    def test_table_block_is_kept_intact(self):
        """<table> 同为块级结构，逐行包 <p> 会打散行列。"""
        table = "<table><tr><th>页面</th><th>判定</th></tr>\n<tr><td>列表页</td><td>正常</td></tr></table>"
        out = z._steps_html("对照矩阵：\n" + table)
        self.assertIn(table, out)

    def test_plain_text_lines_still_wrapped(self):
        """白名单外的普通行仍按老行为逐行包 <p>，避免把纯文本步骤排版改坏。"""
        out = z._steps_html("1. 进入模块\n2. 点击查询")
        self.assertIn("<p>[步骤]</p><p>1. 进入模块</p><p>2. 点击查询</p>", out)

    def test_actual_section_blocks_are_intact(self):
        """actual/expected 走同一兜底排版，多行 <pre> 同样不能被拆。"""
        out = z._steps_html("s", actual="报错日志：\n<pre>Uncaught TypeError: x\n    at add.tsx:1</pre>")
        self.assertIn("<pre>Uncaught TypeError: x\n    at add.tsx:1</pre>", out)

    def test_html_lines_are_not_double_wrapped(self):
        """整行已是 HTML 时不能再包 <p>，否则造出 <p><h3>…</h3></p> 非法嵌套（行间换行不影响渲染）。"""
        out = z._steps_html("<h3>前置条件</h3>\n<p>账号 权限测试</p>")
        self.assertIn("<h3>前置条件</h3>\n<p>账号 权限测试</p>", out)
        self.assertNotIn("<p><h3>", out)

    def test_expected_actual_sections(self):
        out = z._steps_html("s", expected="e", actual="a")
        self.assertIn("[步骤]", out)
        self.assertIn("[结果]", out)
        self.assertIn("[期望]", out)


class TestCleanHtmlForMd(unittest.TestCase):
    def test_entities_are_unescaped(self):
        """_steps_html 会 escape() 报文，只剥标签不反转义就会看到字面 &lt; / &nbsp;。"""
        out = z._clean_html_for_md("<p>a&nbsp;b &lt;x&gt; &amp;c</p>")
        self.assertIn("<x>", out)
        self.assertIn("&c", out)
        self.assertNotIn("&lt;", out)
        self.assertNotIn("&nbsp;", out)

    def test_unparseable_img_is_not_dropped(self):
        out = z._clean_html_for_md("<p>前</p><img><p>后</p>")
        self.assertIn("后", out)
        self.assertIn("图片", out)

    def test_img_with_src_becomes_markdown(self):
        out = z._clean_html_for_md('<p><img src="http://x/1.png" /></p>')
        self.assertIn("![截图](http://x/1.png)", out)

    def test_empty(self):
        self.assertEqual(z._clean_html_for_md(""), "")


class TestMdCell(unittest.TestCase):
    def test_pipe_is_escaped(self):
        """字面 | 会截断 Markdown 表格整行，让后续所有列错位。"""
        self.assertEqual(z._md_cell("a|b"), "a\\|b")

    def test_none_and_newline(self):
        self.assertEqual(z._md_cell(None), "")
        self.assertEqual(z._md_cell("a\nb"), "a b")


# ─────────────────── 2. 纯函数：取值解析 ───────────────────

class TestResolveType(unittest.TestCase):
    def test_known(self):
        self.assertEqual(z._resolve_type("性能问题"), "performance")
        self.assertEqual(z._resolve_type("codeerror"), "codeerror")

    def test_empty_defaults(self):
        self.assertEqual(z._resolve_type(""), z.TYPE_DEFAULT)

    def test_unknown_raises_instead_of_silent_degrade(self):
        """旧实现把未收录类型静默降级成 codeerror，工具仍返回 [ok]，用户以为按原类型建单。"""
        with self.assertRaises(ValueError):
            z._resolve_type("性能优化")


class TestNormSeverity(unittest.TestCase):
    def test_numbers_and_names(self):
        self.assertEqual(z._norm_severity(2), 2)
        self.assertEqual(z._norm_severity("3"), 3)
        self.assertEqual(z._norm_severity("一般"), 3)

    def test_invalid(self):
        for bad in (0, 5, "致命5", "", None):
            with self.assertRaises(ValueError):
                z._norm_severity(bad)


class TestDeadline(unittest.TestCase):
    def test_expired_immediately(self):
        self.assertTrue(z._Deadline(0).expired)

    def test_remaining_is_positive(self):
        self.assertGreaterEqual(z._Deadline(10).remaining(), 1.0)


# ─────────────────── 3. 客户端：认证与重试 ───────────────────

class TestAuth(unittest.TestCase):
    def test_bad_credentials_do_not_recurse(self):
        """旧实现 _req(401) → login() → _req(401) → login() 无限递归（实测 12 次请求被打满）。"""
        c = make_client(lambda m, u, kw, n: FakeResp(401, {"error": "Unauthorized"}))
        with self.assertRaises(RuntimeError) as ctx:
            c.login()
        self.assertNotIsInstance(ctx.exception, RecursionError)
        self.assertLessEqual(len(c.s.calls), 3, "不应出现递归式狂发请求")

    def test_refresh_retry_uses_the_new_token(self):
        """旧实现在循环外拍好 headers，重试仍带旧 token → 再撞 401，刷新形同虚设。"""
        def handler(method, url, kw, n):
            if url.endswith("/tokens"):
                return FakeResp(201, {"token": "NEW"})
            return FakeResp(200, {"id": 1}) if token_of({"kw": kw}) == "NEW" \
                else FakeResp(401, {"error": "expired"})
        c = make_client(handler)
        r = c._req("GET", "/bugs/1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("NEW", [token_of(x) for x in c.s.calls])

    def test_connection_error_retried_once(self):
        def handler(m, u, kw, n):
            raise requests.exceptions.ConnectionError("refused")
        c = make_client(handler)
        with self.assertRaises(RuntimeError):
            c._req("GET", "/products")
        self.assertEqual(len(c.s.calls), z.RETRY + 1)

    def test_read_timeout_is_not_retried(self):
        """读超时重试只会让墙钟时间翻倍并撞穿 MCP 客户端 60s 上限。"""
        def handler(m, u, kw, n):
            raise requests.exceptions.ReadTimeout("slow")
        c = make_client(handler)
        with self.assertRaises(RuntimeError):
            c._req("GET", "/products")
        self.assertEqual(len(c.s.calls), 1)


class TestCreateBugFallback(unittest.TestCase):
    def test_no_fallback_on_validation_error(self):
        """旧实现任何非 2xx 都改走 POST /bugs：首次已落库却返回 5xx 时会**重复建单**。"""
        c = make_client(lambda m, u, kw, n: FakeResp(400, {"error": "bad severity"}))
        with self.assertRaises(RuntimeError) as ctx:
            c.create_bug("40", {"title": "t"})
        self.assertEqual(len(c.s.calls), 1, "非 404/405 不应触发兜底")
        self.assertIn("重复建单", str(ctx.exception))

    def test_fallback_only_on_404_and_reports_both(self):
        def handler(m, u, kw, n):
            if "/products/40/bugs" in u:
                return FakeResp(404, {"error": "no route"})
            return FakeResp(500, {"error": "boom"})
        c = make_client(handler)
        with self.assertRaises(RuntimeError) as ctx:
            c.create_bug("40", {"title": "t"})
        msg = str(ctx.exception)
        self.assertIn("HTTP 404", msg)
        self.assertIn("HTTP 500", msg)


# ─────────────────── 4. 客户端：分页 / 解析 ───────────────────

class TestAllBugs(unittest.TestCase):
    def test_dedupe_and_stop_when_page_ignored(self):
        """服务端忽略 page 时，旧实现会把同一页重复追加到 60 次。"""
        page = {"bugs": [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}], "total": 99}
        c = make_client(lambda m, u, kw, n: FakeResp(200, page))
        bugs, total, truncated = c.all_bugs("40", page_size=2)
        self.assertEqual([b["id"] for b in bugs], [1, 2])
        self.assertFalse(truncated)
        self.assertLessEqual(len(c.s.calls), 3)

    def test_hits_page_cap_marks_truncated(self):
        def handler(m, u, kw, n):
            base = n * 10
            return FakeResp(200, {"bugs": [{"id": base + i} for i in range(2)]})
        c = make_client(handler)
        bugs, _total, truncated = c.all_bugs("40", page_size=2, max_pages=3)
        self.assertEqual(len(bugs), 6)
        self.assertTrue(truncated)


class TestListBugTitles(unittest.TestCase):
    def test_paginates_even_when_total_is_missing(self):
        """旧实现以 total 为唯一终止条件：total 缺失时 page*100>=0 立即为真 → 只取首页 →
        更早的标题被当成"新单" → submit_bugs_from_xlsx **重复建单**。"""
        big = [{"title": f"t{i}"} for i in range(z.BUG_PAGE_SIZE)]
        small = [{"title": "tail1"}, {"title": "tail2"}]

        def handler(m, u, kw, n):
            return FakeResp(200, {"bugs": big if n == 1 else small})   # 无 total 字段
        c = make_client(handler)
        titles = c.list_bug_titles("40")
        self.assertEqual(len(titles), z.BUG_PAGE_SIZE + 2)


class TestFindStory(unittest.TestCase):
    def test_nonexistent_id_is_rejected(self):
        """旧实现 404 也能 _json 解析成功，于是把不存在的 ID 当成有效需求返回。"""
        c = make_client(lambda m, u, kw, n: FakeResp(404, {"error": "not found"}))
        self.assertEqual(c.find_story("40", "18058"), (None, ""))

    def test_http_400_means_not_found(self):
        """实测禅道对不存在的需求返回 400 + {"error":"error"}，不是 404。"""
        c = make_client(lambda m, u, kw, n: FakeResp(400, {"error": "error"}))
        self.assertEqual(c.find_story("40", "99999999"), (None, ""))

    def test_empty_title_is_rejected(self):
        c = make_client(lambda m, u, kw, n: FakeResp(200, {"id": 18058}))
        self.assertEqual(c.find_story("40", "18058"), (None, ""))

    def test_valid_id(self):
        c = make_client(lambda m, u, kw, n: FakeResp(200, {"id": 18058, "title": "审批单列表"}))
        self.assertEqual(c.find_story("40", "18058"), (18058, "审批单列表"))

    def test_server_error_raises_not_silently_notfound(self):
        """旧实现 `except Exception: return None, ""` 把接口故障伪装成"需求不存在"。"""
        c = make_client(lambda m, u, kw, n: FakeResp(500, {"error": "boom"}))
        with self.assertRaises(RuntimeError):
            c.find_story("40", "18058")

    def test_keyword_hit(self):
        c = make_client(lambda m, u, kw, n: FakeResp(
            200, {"stories": [{"id": 7, "title": "审批单列表"}], "total": 1}))
        self.assertEqual(c.find_story("40", "审批单"), (7, "审批单列表"))


class TestFindUser(unittest.TestCase):
    def test_empty_user_list_is_not_cached(self):
        """旧实现 `if self._users_cache is None` —— 空列表不是 None，会被永久缓存，
        之后即使传入真实 bug_id 也不再重试。"""
        c = make_client(lambda m, u, kw, n: FakeResp(200, {"users": []}))
        c.list_users()
        self.assertFalse(c._users_cache)

    def test_cjk_name_unresolvable_raises(self):
        """旧实现无条件 `return ref_strip`，于是把中文姓名当账号发出去（静默错误）。"""
        c = make_client(lambda m, u, kw, n: FakeResp(200, {"users": []}))
        with self.assertRaises(ValueError):
            c.find_user("张三", bug_id=65532)

    def test_ascii_account_passes_through(self):
        c = make_client(lambda m, u, kw, n: FakeResp(200, {"users": []}))
        self.assertEqual(c.find_user("zhaohaoyuan", bug_id=1), "zhaohaoyuan")

    def test_resolves_realname_from_cache(self):
        c = make_client(lambda m, u, kw, n: FakeResp(200, {"users": []}))
        c._users_cache = [{"account": "zhaohaoyuan", "realname": "赵浩源"}]
        self.assertEqual(c.find_user("赵浩源"), "zhaohaoyuan")

    def test_zero_bug_id_still_queries_the_page(self):
        """实测 bug-activate-0.html 同样带 assignedTo 下拉（471 个选项）。

        本机 REST /users 无权限、_auto_bug_id() 又常拿不到 ID，这条 0 号通道往往是
        唯一来源；曾因加了 `if not bug_id: return []` 把用户解析整体打断。
        """
        html = "<select name='assignedTo'><option value='zhaohaoyuan'>赵浩源</option></select>"
        c = make_client(lambda m, u, kw, n: FakeResp(200, text=html))
        c._web_logged = True          # 跳过真实登录
        users = c._users_from_web(0)
        self.assertTrue(c.s.calls, "bug_id=0 也必须真的请求页面，不能短路")
        self.assertEqual(users, [{"account": "zhaohaoyuan", "realname": "赵浩源",
                                  "role": None, "dept": None}])

    def test_auto_bug_id_falls_back_to_product_endpoint(self):
        """`GET /bugs?limit=1` 在 17.1 上不存在，必须再用 /products/{id}/bugs 兜一层。"""
        def handler(m, u, kw, n):
            if "/products/40/bugs" in u:
                return FakeResp(200, {"bugs": [{"id": 65548}]})
            return FakeResp(404, {"error": "no route"})
        c = make_client(handler)
        self.assertEqual(c._auto_bug_id(), 65548)


# ─────────────────── 5. Web 通道：假成功判定 ───────────────────

class TestWebOk(unittest.TestCase):
    def setUp(self):
        self.c = make_client(lambda m, u, kw, n: FakeResp(200, {}))

    def test_http_200_with_result_fail_is_failure(self):
        """禅道表单校验/权限失败返回 200 + {"result":"fail"}，旧实现只看 200 就报 [ok]。"""
        self.assertFalse(self.c._web_ok(FakeResp(200, {"result": "fail"})))

    def test_login_page_html_is_failure_and_resets_flag(self):
        self.c._web_logged = True
        self.assertFalse(self.c._web_ok(FakeResp(200, text='<html><form id="loginForm" '
                                                       'name="account"></form></html>')))
        self.assertFalse(self.c._web_logged, "会话失效应置位，以便下次重登")

    def test_success(self):
        self.assertTrue(self.c._web_ok(FakeResp(200, {"result": "success"})))
        self.assertTrue(self.c._web_ok(FakeResp(200, {"id": 1})))

    def test_non_200(self):
        self.assertFalse(self.c._web_ok(FakeResp(500, {"result": "success"})))


# ─────────────────── 6. 工具包装层 ───────────────────

class TestToolWrapper(unittest.TestCase):
    def test_exception_becomes_readable_error_text(self):
        """未捕获异常会被 FastMCP 变成协议层错误，客户端只看到 "tool call failed"。"""
        original = z._get_client

        def boom():
            raise RuntimeError("禅道未连接")
        z._get_client = boom
        try:
            out = z.whoami()
        finally:
            z._get_client = original
        self.assertTrue(out.startswith("[error]"), out)
        self.assertIn("禅道未连接", out)

    def test_signature_preserved_for_schema(self):
        import inspect
        self.assertIn("bug_id", inspect.signature(z.get_bug).parameters)
        self.assertIn("full_steps", inspect.signature(z.get_bug).parameters)


# ─────────────────── 7. xlsx 清单解析 ───────────────────

class TestLoadBugsFromXlsx(unittest.TestCase):
    def _write(self, headers, rows):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "BUG清单"
        ws.append(headers)
        for r in rows:
            ws.append(r)
        p = Path(tempfile.mkdtemp()) / "t.xlsx"
        wb.save(p)
        return p

    def test_missing_column_raises_readable_error(self):
        p = self._write(["BUG标题", "所属模块"], [["t1", "m"]])
        with self.assertRaises(ValueError) as ctx:
            z._load_bugs_from_xlsx(p)
        self.assertIn("缺少必需列", str(ctx.exception))

    def test_chinese_severity_is_accepted(self):
        """旧实现直接 int(r[idx["严重程度"]])，写「一般」会 ValueError 让整批炸掉。"""
        p = self._write(
            ["BUG标题", "所属模块", "严重程度", "优先级", "BUG类型", "重现步骤"],
            [["标题A", "模块A", "一般", "高", "代码错误", "1. 步骤"]],
        )
        bugs = z._load_bugs_from_xlsx(p)
        self.assertEqual(len(bugs), 1)
        self.assertEqual(z._norm_severity(bugs[0]["severity"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
