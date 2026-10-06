import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.publication.compiler import compile_latex
from src.publication.render_latex import escape_latex, escape_math


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
