import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.publication.compiler import compile_latex
from src.publication.render_latex import escape_latex, escape_math, render_latex
from src.publication.schemas import Block, Manuscript, Section


class PDFCompilationTest(unittest.TestCase):
    def run_compiler(self, outcomes, output_elsewhere=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tex = root / "manuscript.tex"
            tex.write_text("test", encoding="utf-8")
            output = root / "build" if output_elsewhere else root
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                Path(args[args.index("-output-directory") + 1], "manuscript.pdf").write_bytes(b"%PDF-1.7\nfixture")
                code, log = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
                return subprocess.CompletedProcess(args, code, stdout=log, stderr="")
            with patch("src.publication.compiler.subprocess.run", side_effect=run), \
                 patch("src.publication.compiler.time.sleep"):
                result = compile_latex(str(tex), workdir=str(output))
            return result, len(calls)

    def test_small_pdf_and_custom_output_directory_are_supported(self):
        (ok, _), _ = self.run_compiler([(0, "Output written")], output_elsewhere=True)
        self.assertTrue(ok)

    def test_compiler_error_never_accepts_partial_pdf(self):
        (ok, _), calls = self.run_compiler([(1, "! Missing $ inserted.")])
        self.assertFalse(ok)
        self.assertEqual(calls, 1)

    def test_windows_lock_is_retried(self):
        (ok, _), calls = self.run_compiler([(1, "Unable to open PDF"), (0, "Output written")])
        self.assertTrue(ok)
        self.assertEqual(calls, 2)

    def test_unresolved_citations_are_not_success(self):
        (ok, _), _ = self.run_compiler([(0, "There were undefined references")])
        self.assertFalse(ok)

    def test_negligible_box_warning_is_not_a_compilation_failure(self):
        (ok, log), _ = self.run_compiler([(0, "Overfull \\hbox (1.05pt too wide) at line 1\nOutput written")])
        self.assertTrue(ok)
        self.assertIn("Overfull", log)

    def test_severe_box_overflow_remains_blocking(self):
        (ok, log), _ = self.run_compiler([(0, "Overfull \\hbox (111.16pt too wide) at line 118\nOutput written")])
        self.assertFalse(ok)
        self.assertIn("111.16", log)

    def test_test_machine_math_escape_fixes_are_preserved(self):
        text = escape_latex("{0,1}^667 M^T c_i λ ∈")
        self.assertIn(r"$\{0,1\}^{667}$", text)
        self.assertIn("$M^{T}$", text)
        self.assertIn("$c_{i}$", text)
        self.assertIn(r"$\lambda$", text)
        self.assertNotIn("\x00", text)

    def test_already_escaped_specials_are_not_escaped_twice(self):
        r"""回归 (proj-muvx3j7ima1z): 模型在公式里自己写了 `\#`。

        无条件再转一次会得到 `\\#` —— `\\` 是换行, `#` 成了裸宏参数字符, xelatex 报
        ``! You can't use `macro parameter character #' in display math mode.``,
        30KB 的稿件只写出 **3 页** (全文 10 页)。
        """
        formula = escape_math(r"\#\{i:s_i(k)=+1\}=\#\{i:s_i(k)=-1\}")
        self.assertNotIn("\\\\#", formula)
        self.assertEqual(formula.count(r"\#"), 2)

        text = escape_latex(r"基数 \#\{i\} 与 100\% 与 \& 与 \_x")
        for kept in (r"\#", r"\%", r"\&", r"\_"):
            self.assertIn(kept, text)
        self.assertNotIn("\\\\#", text)

    def test_superscript_subscript_pairs_and_greek_bases_are_recognised(self):
        self.assertIn(r"$\Sigma_{t=0}^{166}$", escape_latex("Σ_{t=0}^{166}"))
        self.assertIn(r"$\{+1,-1\}^{668}$", escape_latex("{+1,−1}^668"))
        # 指数里的 Unicode 乘号也要转成数学命令, 否则留在数学模式里
        self.assertIn(r"^{668\times668}", escape_latex("x^{668×668}"))
        self.assertNotIn("textasciicircum", escape_latex("Σ_{t=0}^{166}"))

    def test_recently_missing_glyphs_are_mapped(self):
        r"""`↦`/`ᵀ`/`⌊`/`⌋` 是本轮日志实际报出的缺字形; `≢`/`⁸` 是重渲染后新暴露的。"""
        text = escape_latex("映射 ↦ 与 Mᵀ 与 ⌊x⌋ 与 ≢ 与 10⁸")
        for absent in ("↦", "ᵀ", "⌊", "⌋", "≢", "⁸"):
            self.assertNotIn(absent, text)
        for expected in (r"$\mapsto$", r"$^{\mathrm{T}}$", r"$\lfloor$",
                         r"$\not\equiv$", r"$^{8}$"):
            self.assertIn(expected, text)

    def test_formula_escape_does_not_nest_math_modes(self):
        formula = escape_math("λ−x_i，y^2")
        self.assertNotIn("$", formula)
        self.assertIn(r"\lambda", formula)
        self.assertIn("x_i", formula)
        self.assertIn("y^2", formula)

    def test_model_authored_tags_become_one_numbered_equation_each(self):
        r"""回归（transfer-eval2 / snap-c42273b2）：模型在一个数学块里写多条带编号的公式。

        实测：模型把多条公式塞进一个块，用 `;`/`\qquad` 分隔，每条自带 `\tag`；
        `_display_math` 又把它们装进**同一个** `aligned`，整块落在自动编号的
        `equation` 里 → amsmath 只允许每个公式一个 `\tag`，报
        ``! Package amsmath Error: Multiple \tag.``，**整篇编译失败、该轮没有 PDF**。

        修法是按 `\tag` 边界拆成逐条 `equation*`。这里同时钉住"**保留模型编号**"：
        正文有 40 处 `见式（3.x）` 依赖它，删掉 `\tag` 让渲染器重排会让引用全部指错。
        """
        block = Block(math=(
            r"D_{\mathrm{eff}}\equiv(1-f)D_{b}+fD_{gb},\quad D_{b}>0\tag{3.5};"
            r"\qquad f\approx\frac{2\delta}{d}\tag{3.6}"))
        tex = render_latex(Manuscript(sections=[Section(heading="模型", blocks=[block])]))
        self.assertEqual(tex.count(r"\begin{equation*}"), 2)
        self.assertEqual(tex.count(r"\tag{"), 2)
        self.assertIn(r"\tag{3.5}", tex)
        self.assertIn(r"\tag{3.6}", tex)
        # 不得再有自动编号的 equation —— 那正是 `Multiple \tag` 的来源
        self.assertNotIn(r"\begin{equation}", tex)
        # 拆分后行首不应残留分隔符
        self.assertNotIn(r"\qquad f\approx", tex.split(r"\tag{3.5}")[1])

    def test_three_tags_split_into_three_equations(self):
        block = Block(math=r"a=b\tag{1};\qquad c=d\tag{2};\qquad e=f\tag{3}")
        tex = render_latex(Manuscript(sections=[Section(heading="模型", blocks=[block])]))
        self.assertEqual(tex.count(r"\begin{equation*}"), 3)
        self.assertEqual(tex.count(r"\tag{"), 3)

    def test_untagged_math_keeps_automatic_numbering(self):
        block = Block(math=r"E=mc^2")
        tex = render_latex(Manuscript(sections=[Section(heading="模型", blocks=[block])]))
        self.assertEqual(tex.count(r"\begin{equation}"), 1)
        self.assertNotIn(r"\tag", tex)

    def test_glued_control_word_gets_separated(self):
        r"""回归（transfer-eval2）：模型写 `$0\leqz\leqL$`，TeX 把 `\leqz` 当成未定义命令。

        报 ``! Undefined control sequence.``；`-halt-on-error` 只报第一个，但整篇失败。
        该稿件共 10 处：`\leqz`/`\leqL`/`\leqRH`/`\leqa`/`\leqf`/`\geqN`/`\lld`。
        """
        out = escape_math(r"0\leqz\leqL")
        self.assertIn(r"\leq z", out)
        self.assertIn(r"\leq L", out)
        self.assertNotIn(r"\leqz", out)
        self.assertEqual(escape_math(r"\delta_gb\ll d"), r"\delta_gb\ll d")  # 已合法, 不动

    def test_known_long_command_is_not_split(self):
        # 只切"已知命令的最长前缀"，整词命中白名单时必须原样保留
        for kept in (r"\textwidth", r"\mathrm{TFL}", r"\theta", r"\leq", r"\infty"):
            self.assertEqual(escape_math(kept), kept)

    def test_unknown_macro_is_left_alone(self):
        # 未知宏不自作主张, 交给编译如实暴露
        self.assertEqual(escape_math(r"\hobx{x}"), r"\hobx{x}")

    def test_tag_is_hoisted_out_of_aligned_rows(self):
        r"""回归（transfer-eval2）：`\tag` 被 `aligned` 卷进内部环境。

        `_display_math` 按 `;`/`\qquad` 拆行并用 `aligned` 包住各行时，模型写在公式
        末尾的编号会落进内部环境 → ``! Package amsmath Error: \tag not allowed here.``
        编号属于**公式层**，必须提到 `\end{aligned}` 之后。
        """
        from src.publication.render_latex import _display_math
        body = (r"\frac{\partial C_w}{\partial t}=\nabla\cdot\left(D_{\mathrm{eff}}"
                r"\nabla C_w\right)-R_{\mathrm{hyd}},\qquad "
                r"R_{\mathrm{hyd}}=R_{\mathrm{hyd}}\left(C_w,\phi_p,\theta_h,n,p\right)\tag{3.1}")
        out = _display_math(body)
        self.assertIn(r"\begin{aligned}", out)
        self.assertIn(r"\end{aligned}\tag{3.1}", out)
        self.assertNotIn(r"\tag{3.1}\end{aligned}", out)

    def test_tag_inside_model_written_aligned_is_hoisted(self):
        from src.publication.render_latex import _display_math
        out = _display_math(r"\begin{aligned}a&=b\\ c&=d\tag{1}\end{aligned}")
        self.assertIn(r"\end{aligned}\tag{1}", out)
        self.assertNotIn(r"\tag{1}\end{aligned}", out)

    def test_math_notation_inside_text_group_is_wrapped(self):
        r"""回归（transfer-eval2）：`\text{}` 处于文本模式，里面有数学记法就报
        ``! Missing $ inserted.``。实测四种写法：`\text{中文 \delta x 中文}`、
        `\text{中文 x_i 中文}`、`\text{中文 \delta_{gb} 中文}` 全部失败，只有
        `\text{中文 $x_i$ 中文}` 通过 —— 故把数学片段包进 `$...$`。
        """
        out = escape_math(r"\text{中文 x_i 中文}")
        self.assertIn("$", out)
        self.assertRegex(out, r"\$[^$]*x_i[^$]*\$")
        out2 = escape_math(r"\text{柱状晶近似,\ \delta_gb\ll d,\ 需核验}")
        self.assertRegex(out2, r"\$[^$]*\\delta_gb[^$]*\$")

    def test_text_group_without_math_is_untouched(self):
        # 纯文本与含文本命令的片段都不动（塞进数学模式反而会引入新错误）
        self.assertEqual(escape_math(r"\text{仅中文与 100\% 数字}"),
                         r"\text{仅中文与 100\% 数字}")
        self.assertEqual(escape_math(r"\text{\textbf{加粗}说明}"),
                         r"\text{\textbf{加粗}说明}")

    def test_failed_compile_does_not_leave_a_stale_manuscript_pdf(self):
        r"""回归 R-009（transfer-eval2）：编译失败时 xelatex 会留下**部分** PDF。

        包里有 `manuscript.pdf`（实测 182KB）而 manifest 的 `pdf` 字段为空 ——
        清单是诚实的，但只按文件列表读包的人会被误导。失败后改名为
        `manuscript.failed.pdf`：保留诊断价值，不再冒充交付件。
        """
        from src.graph.team_session import TeamSession
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)
            (package / "manuscript.pdf").write_bytes(b"%PDF-1.7\npartial" * 200)
            with patch("src.publication.compiler.compile_latex",
                       return_value=(False, r"! Package amsmath Error: Multiple \tag.")):
                status, _ = TeamSession._compile_package(None, package, "tex")
            self.assertTrue(status.startswith("failed"), status)
            self.assertFalse((package / "manuscript.pdf").exists())
            self.assertTrue((package / "manuscript.failed.pdf").exists())

    def test_successful_compile_keeps_the_delivered_pdf(self):
        from src.graph.team_session import TeamSession
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)

            def fake_compile(path, workdir=None, **kwargs):
                Path(workdir or Path(path).parent, "manuscript.pdf").write_bytes(
                    b"%PDF-1.7\n" + b"x" * 4096)
                return True, "Output written"

            with patch("src.publication.compiler.compile_latex", side_effect=fake_compile):
                status, _ = TeamSession._compile_package(None, package, "tex")
            self.assertEqual(status, "ok")
            self.assertTrue((package / "manuscript.pdf").exists())
            self.assertFalse((package / "manuscript.failed.pdf").exists())
