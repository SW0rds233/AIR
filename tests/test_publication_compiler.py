"""出版编译器只对瞬时文件占用重试，不对缺失工具空等。"""

from src.publication import compiler


def test_missing_latex_engine_fails_without_retry(tmp_path, monkeypatch):
    tex = tmp_path / "paper.tex"
    tex.write_text("test", encoding="utf-8")
    calls = []

    def missing(*args, **kwargs):
        calls.append(args)
        raise FileNotFoundError("xelatex")

    monkeypatch.setattr(compiler.subprocess, "run", missing)
    monkeypatch.setattr(compiler.time, "sleep",
                        lambda _: (_ for _ in ()).throw(AssertionError("不应等待重试")))
    ok, log = compiler.compile_latex(str(tex))
    assert ok is False
    assert "未安装" in log
    assert len(calls) == 1
