"""prosperity-picking 状态脚本（prosperity_state.py）的单元测试。

重点覆盖四块：
1. add 自动建状态文件 + 固定字段 + 板块 id 校验（ASCII 短代号，作观察仓 PS param 值）；
2. review 回写复核结论 / rm 移入 history 留痕 / 重复与不存在的报错路径；
3. show 的两态输出（在跟板块清单 / 「当前没有跟踪中的景气板块」判定短语）；
4. 序列化契约：条目顶格 + 子字段 2 空格缩进（PyYAML safe_dump 同风格），
   读宽容任意缩进，头注释保留。

临时目录说明：不用 pytest 的 tmp_path（其 basetemp 清理走 ``\\?\\`` 长路径前缀，
会被 DSH 会话文件沙箱以 WinError 5 拒绝），改用工作区 .tmp/ 下自管理目录（已 gitignore）。
"""
import importlib.util
import shutil
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "src" / "workspace-init" / "prosperity_state.py"


def _load(path: Path, name: str):
    """从文件路径加载独立脚本模块（须先注册进 sys.modules，与 test_manage_watchlist 同理）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ps = _load(SCRIPT, "prosperity_state")


@pytest.fixture
def tmp_dir():
    """每次测试一个独立临时目录；teardown 尽力清理（沙箱拒绝删除也不报错）。"""
    d = REPO / ".tmp" / "test-prosperity-state" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _st(tmp_dir: Path) -> Path:
    return tmp_dir / "state.yaml"


# ── 板块 id 校验 ─────────────────────────────────────────────────────────────


def test_validate_id_ok():
    assert ps.validate_id("ai-compute") == "ai-compute"
    assert ps.validate_id("robotics") == "robotics"
    assert ps.validate_id("pd") == "pd"


def test_validate_id_rejects_non_ascii_or_upper():
    # 中文 / 大写 / 下划线 / 空格 / 过长，都应拒绝（PS param 值须 ASCII 短代号）
    for bad in ("AI算力", "AI", "ai_compute", "ai compute", "-ai", "a" * 25, ""):
        with pytest.raises(SystemExit):
            ps.validate_id(bad)


# ── add：自动建文件 + 字段 + 幂等边界 ────────────────────────────────────────


def test_add_creates_file_with_fixed_fields(tmp_dir):
    st = _st(tmp_dir)
    rc = ps.main(
        [
            "--state", str(st), "--now", "2025-06-03",
            "add", "ai-compute", "--name", "AI算力", "--reason", "盈利上修+订单饱满",
        ]
    )
    assert rc == 0
    assert st.exists()
    text = st.read_text(encoding="utf-8")
    assert "sectors:" in text
    assert "history:" in text
    assert "- id: ai-compute" in text
    assert 'name: "AI算力"' in text
    assert "added_at: 2025-06-03" in text
    assert "last_review: 2025-06-03" in text
    assert 'reason: "盈利上修+订单饱满"' in text


def test_add_duplicate_rejected(tmp_dir):
    st = _st(tmp_dir)
    ps.main(["--state", str(st), "add", "ai-compute", "--name", "AI算力", "--reason", "r1"])
    with pytest.raises(SystemExit):
        ps.main(["--state", str(st), "add", "ai-compute", "--name", "AI算力", "--reason", "r2"])


def test_add_readd_after_rm_allowed(tmp_dir):
    """剔除后重新景气可再 add：history 留旧痕，sectors 建新条目。"""
    st = _st(tmp_dir)
    ps.main(["--state", str(st), "--now", "2025-01-10", "add", "semi", "--name", "半导体",
             "--reason", "r1"])
    ps.main(["--state", str(st), "--now", "2025-03-01", "rm", "semi", "--reason", "预期下修"])
    rc = ps.main(["--state", str(st), "--now", "2025-06-01", "add", "semi", "--name", "半导体",
                 "--reason", "r2"])
    assert rc == 0
    _, sections = ps.parse_text(st.read_text(encoding="utf-8"))
    assert len(sections["sectors"]) == 1
    assert sections["sectors"][0]["added_at"] == "2025-06-01"
    assert len(sections["history"]) == 1
    assert sections["history"][0]["removed_at"] == "2025-03-01"


# ── review / rm ──────────────────────────────────────────────────────────────


def test_review_updates_note_and_date(tmp_dir):
    st = _st(tmp_dir)
    ps.main(["--state", str(st), "--now", "2025-06-03", "add", "ai-compute", "--name", "AI算力",
             "--reason", "r"])
    rc = ps.main(
        ["--state", str(st), "--now", "2025-09-01", "review", "ai-compute",
         "--note", "景气点：订单饱满；风险点：估值分位偏高"]
    )
    assert rc == 0
    _, sections = ps.parse_text(st.read_text(encoding="utf-8"))
    sec = sections["sectors"][0]
    assert sec["last_review"] == "2025-09-01"
    assert sec["added_at"] == "2025-06-03"  # 首加日期不动
    assert sec["note"] == "景气点：订单饱满；风险点：估值分位偏高"


def test_review_missing_id_rejected(tmp_dir):
    st = _st(tmp_dir)
    ps.main(["--state", str(st), "add", "ai-compute", "--name", "AI算力", "--reason", "r"])
    with pytest.raises(SystemExit):
        ps.main(["--state", str(st), "review", "robotics", "--note", "n"])


def test_rm_moves_to_history_with_reason(tmp_dir):
    st = _st(tmp_dir)
    ps.main(["--state", str(st), "--now", "2025-01-10", "add", "semi", "--name", "半导体",
             "--reason", "盈利上行"])
    ps.main(["--state", str(st), "--now", "2025-06-01", "review", "semi", "--note", "still ok"])
    rc = ps.main(
        ["--state", str(st), "--now", "2025-09-01", "rm", "semi", "--reason", "预期密集下修"]
    )
    assert rc == 0
    _, sections = ps.parse_text(st.read_text(encoding="utf-8"))
    assert sections["sectors"] == []
    assert len(sections["history"]) == 1
    h = sections["history"][0]
    assert h["id"] == "semi"
    assert h["name"] == "半导体"
    assert h["added_at"] == "2025-01-10"
    assert h["removed_at"] == "2025-09-01"
    assert h["reason"] == "盈利上行"  # 入选原因保留
    assert h["remove_reason"] == "预期密集下修"


def test_rm_missing_id_rejected(tmp_dir):
    st = _st(tmp_dir)
    with pytest.raises(SystemExit):
        ps.main(["--state", str(st), "rm", "semi", "--reason", "r"])


# ── show：两态输出 ───────────────────────────────────────────────────────────


def test_show_without_state_file(tmp_dir, capsys):
    rc = ps.main(["--state", str(_st(tmp_dir))])
    assert rc == 0
    out = capsys.readouterr().out
    assert ps.EMPTY_MARK in out  # 面板/指令据此判定走初始化或拒绝分支
    assert "状态文件不存在" in out


def test_show_lists_sectors_and_history(tmp_dir, capsys):
    st = _st(tmp_dir)
    ps.main(["--state", str(st), "--now", "2025-06-03", "add", "ai-compute", "--name", "AI算力",
             "--reason", "盈利上修"])
    ps.main(["--state", str(st), "--now", "2025-06-03", "add", "robotics", "--name", "机器人",
             "--reason", "订单饱满"])
    ps.main(["--state", str(st), "--now", "2025-09-01", "rm", "robotics", "--reason", "预期下修"])
    rc = ps.main(["--state", str(st)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "当前跟踪景气板块 1 个" in out
    assert "ai-compute" in out and "AI算力" in out
    assert "入选原因: 盈利上修" in out
    assert "已剔除板块 1 个" in out
    assert "robotics" in out and "预期下修" in out


def test_show_empty_state_file(tmp_dir, capsys):
    st = _st(tmp_dir)
    ps.main(["--state", str(st), "add", "semi", "--name", "半导体", "--reason", "r"])
    ps.main(["--state", str(st), "rm", "semi", "--reason", "r"])
    rc = ps.main(["--state", str(st)])
    assert rc == 0
    out = capsys.readouterr().out
    assert ps.EMPTY_MARK in out
    assert "已剔除板块 1 个" in out  # 历史仍展示


# ── 序列化契约：读宽容 / 写规范 / 头注释保留 ────────────────────────────────


def test_serialize_pyyaml_style(tmp_dir):
    st = _st(tmp_dir)
    note = "景气点：订单+涨价；风险点：拥挤度（含空格/括号）"
    ps.main(["--state", str(st), "add", "ai-compute", "--name", "AI算力", "--reason", "r"])
    ps.main(["--state", str(st), "review", "ai-compute", "--note", note])
    text = st.read_text(encoding="utf-8")
    # 条目恒顶格 + 子字段恒 2 空格（PyYAML safe_dump 同风格）
    assert "\n- id: ai-compute" in text
    assert '\n  name: "AI算力"' in text
    assert "\n  last_review:" in text
    # 特殊字符值带引号且往返一致
    _, sections = ps.parse_text(text)
    assert sections["sectors"][0]["note"] == note


def test_parse_tolerates_indent_variants(tmp_dir):
    """手编 2 空格缩进条目（子级列表缩进）也能读出；重写后规范化。"""
    st = _st(tmp_dir)
    st.write_text(
        "# 手写头注释\nsectors:\n  - id: semi\n    name: 半导体\n    added_at: 2025-01-10\n"
        "    reason: 盈利上行\nhistory:\n",
        encoding="utf-8",
    )
    _, sections = ps.parse_text(st.read_text(encoding="utf-8"))
    assert sections["sectors"][0]["id"] == "semi"
    assert sections["sectors"][0]["name"] == "半导体"
    # 跑一次 add 触发规范化回写
    ps.main(["--state", str(st), "add", "ai-compute", "--name", "AI算力", "--reason", "r"])
    text = st.read_text(encoding="utf-8")
    assert "# 手写头注释" in text  # 头注释保留
    assert "\n- id: semi" in text
    assert "\n- id: ai-compute" in text
