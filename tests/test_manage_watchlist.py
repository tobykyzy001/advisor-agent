"""watchlist-manager 脚本（manage_watchlist.py）的单元测试。

重点覆盖三块：
1. 代码规范化（600519→600519.SH、700→00700.HK 等）与拒绝路径；
2. add 幂等 / set param 透传 / rm / check 退出码；
3. 契约兼容（硬约束，统一 2 空格缩进 = PyYAML safe_dump 风格）：本脚本写出的清单，
   w-bottom-screener / momentum-rotation 的极简解析与量化核心 save_watchlist 三方互通；
   旧 4 空格手编文件仍宽容可读。

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
SCRIPT = REPO / "src" / "workspace-init" / "manage_watchlist.py"
WB_SCRIPT = REPO / "src" / "workspace-init" / "w_bottom_screen.py"
MOM_SCRIPT = REPO / "src" / "workspace-init" / "momentum_strategy.py"


def _load(path: Path, name: str):
    """从文件路径加载独立脚本模块。

    必须先注册进 sys.modules：Python 3.14 的 dataclass 会查
    sys.modules[cls.__module__]，未注册时抛 AttributeError。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mw = _load(SCRIPT, "manage_watchlist")
wb = _load(WB_SCRIPT, "w_bottom_screen_for_test")
mom = _load(MOM_SCRIPT, "momentum_strategy_for_test")


@pytest.fixture
def tmp_dir():
    """每次测试一个独立临时目录；teardown 尽力清理（沙箱拒绝删除也不报错）。"""
    d = REPO / ".tmp" / "test-manage-watchlist" / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _wl(tmp_dir: Path) -> Path:
    return tmp_dir / "watchlist.yaml"


# ── 代码规范化 ───────────────────────────────────────────────────────────────


def test_normalize_code_a_share():
    assert mw.normalize_code("600519") == "600519.SH"
    assert mw.normalize_code("000858") == "000858.SZ"
    assert mw.normalize_code("300750") == "300750.SZ"
    assert mw.normalize_code("688981") == "688981.SH"
    assert mw.normalize_code("430047") == "430047.BJ"
    assert mw.normalize_code("830799") == "830799.BJ"
    assert mw.normalize_code("920001") == "920001.BJ"
    assert mw.normalize_code("600519.SH") == "600519.SH"
    assert mw.normalize_code("000858.sz") == "000858.SZ"


def test_normalize_code_hk():
    assert mw.normalize_code("700") == "00700.HK"
    assert mw.normalize_code("00700") == "00700.HK"
    assert mw.normalize_code("0700.hk") == "00700.HK"
    assert mw.normalize_code("3690.HK") == "03690.HK"


def test_normalize_code_rejects():
    # 9 开头非 920 的 6 位数字（疑似 B 股）应报错，不猜测交易所
    with pytest.raises(SystemExit):
        mw.normalize_code("900123")
    with pytest.raises(SystemExit):
        mw.normalize_code("abc")
    with pytest.raises(SystemExit):
        mw.normalize_code("1234567")
    with pytest.raises(SystemExit):
        mw.normalize_code("60a519.SH")


# ── add：自动建文件 + 字段 + 幂等 ────────────────────────────────────────────


def test_add_creates_file_with_fixed_fields(tmp_dir):
    wl = _wl(tmp_dir)
    rc = mw.main(
        [
            "--watchlist", str(wl), "--now", "2025-06-03",
            "add", "600519", "--name", "贵州茅台", "--note", "等回调到 1500",
            "--source", "stock-valuation",
        ]
    )
    assert rc == 0
    assert wl.exists()
    text = wl.read_text(encoding="utf-8")
    assert "watchlist:" in text
    assert '- ts_code: "600519.SH"' in text
    assert 'name: "贵州茅台"' in text
    assert "market: A" in text
    assert "added_at: 2025-06-03" in text
    assert "source: stock-valuation" in text
    assert "note: " in text


def test_add_hk_market_inferred(tmp_dir):
    wl = _wl(tmp_dir)
    rc = mw.main(["--watchlist", str(wl), "add", "700", "--name", "腾讯控股"])
    assert rc == 0
    text = wl.read_text(encoding="utf-8")
    assert '- ts_code: "00700.HK"' in text
    assert "market: HK" in text


def test_add_idempotent_keeps_first_added_at(tmp_dir):
    wl = _wl(tmp_dir)
    mw.main(["--watchlist", str(wl), "--now", "2025-06-01", "add", "600519", "--name", "A", "--note", "n1"])
    rc = mw.main(["--watchlist", str(wl), "--now", "2025-06-10", "add", "600519", "--note", "n2"])
    assert rc == 0
    text = wl.read_text(encoding="utf-8")
    assert text.count('- ts_code: "600519.SH"') == 1
    assert "added_at: 2025-06-01" in text  # 保留首加日期
    assert "n2" in text  # note 已更新


# ── set：param 透传 + 契约兼容（核心） ──────────────────────────────────────


def test_set_params_and_consumer_compat(tmp_dir):
    wl = _wl(tmp_dir)
    mw.main(["--watchlist", str(wl), "add", "600519", "--name", "贵州茅台", "--note", "等回调"])
    rc = mw.main(["--watchlist", str(wl), "set", "600519", "--BS", "B", "--BS_DATE=2025-06-03"])
    assert rc == 0
    text = wl.read_text(encoding="utf-8")
    # param 键恒 2 空格缩进（消费方 _KEY 认任意缩进，标准格式为 2 空格）
    assert "\n  BS: B" in text
    assert "\n  BS_DATE: 2025-06-03" in text
    # 两个消费方（w-bottom / momentum 的极简解析）都能读出四字段，param 键不破坏解析
    for mod in (wb, mom):
        items = mod.load_watchlist(wl)
        assert len(items) == 1
        assert items[0].ts_code == "600519.SH"
        assert items[0].name == "贵州茅台"
        assert items[0].market == "A"
        assert items[0].note == "等回调"


def test_set_updates_only_named_keys(tmp_dir):
    wl = _wl(tmp_dir)
    mw.main(["--watchlist", str(wl), "add", "600519", "--name", "贵州茅台", "--note", "等回调"])
    mw.main(["--watchlist", str(wl), "set", "600519", "--MR", "3"])
    mw.main(["--watchlist", str(wl), "set", "600519", "--BS", "B"])  # 第二个工具写入，不动 MR
    text = wl.read_text(encoding="utf-8")
    assert "\n  MR: 3" in text
    assert "\n  BS: B" in text
    # 固定字段不受影响
    assert 'name: "贵州茅台"' in text


def test_set_missing_code_rejected(tmp_dir):
    wl = _wl(tmp_dir)
    mw.main(["--watchlist", str(wl), "add", "600519"])
    with pytest.raises(SystemExit):
        mw.main(["--watchlist", str(wl), "set", "000858", "--MR", "3"])


def test_set_rejects_non_ascii_key(tmp_dir):
    wl = _wl(tmp_dir)
    mw.main(["--watchlist", str(wl), "add", "600519"])
    # 中文键消费方解析不出来（会被静默忽略），直接拒绝
    with pytest.raises(SystemExit):
        mw.main(["--watchlist", str(wl), "set", "600519", "--底", "B"])


def test_set_rejects_missing_value(tmp_dir):
    wl = _wl(tmp_dir)
    mw.main(["--watchlist", str(wl), "add", "600519"])
    with pytest.raises(SystemExit):
        mw.main(["--watchlist", str(wl), "set", "600519", "--BS"])
    with pytest.raises(SystemExit):
        mw.main(["--watchlist", str(wl), "set", "600519"])


# ── rm / check ───────────────────────────────────────────────────────────────


def test_rm(tmp_dir):
    wl = _wl(tmp_dir)
    mw.main(["--watchlist", str(wl), "add", "600519"])
    mw.main(["--watchlist", str(wl), "add", "000858"])
    rc = mw.main(["--watchlist", str(wl), "rm", "600519"])
    assert rc == 0
    text = wl.read_text(encoding="utf-8")
    assert "600519.SH" not in text
    assert "000858.SZ" in text
    # 再删 → 不在仓，退出码 1
    assert mw.main(["--watchlist", str(wl), "rm", "600519"]) == 1


def test_check_exit_codes(tmp_dir):
    wl = _wl(tmp_dir)
    mw.main(["--watchlist", str(wl), "add", "600519"])
    assert mw.main(["--watchlist", str(wl), "check", "600519"]) == 0
    assert mw.main(["--watchlist", str(wl), "check", "000858"]) == 1
    # 清单缺失 → 不在仓（退出码 1，不报错）
    assert mw.main(["--watchlist", str(tmp_dir / "none.yaml"), "check", "600519"]) == 1


# ── list 与手编文件规范化 ────────────────────────────────────────────────────


def test_list_default_and_output(tmp_dir, capsys):
    wl = _wl(tmp_dir)
    mw.main(["--watchlist", str(wl), "add", "600519", "--name", "贵州茅台", "--note", "等回调"])
    mw.main(["--watchlist", str(wl), "set", "600519", "--BS", "B"])
    rc = mw.main(["--watchlist", str(wl)])  # 无子命令 → list
    assert rc == 0
    out = capsys.readouterr().out
    assert "600519.SH" in out
    assert "贵州茅台" in out
    assert "BS=B" in out


def test_rewrite_keeps_pyyaml_style(tmp_dir):
    """PyYAML safe_dump 风格（条目顶格、子键 2 空格）就是标准契约格式：
    本脚本读宽容、写入保持 2 空格（仅规范化引号/键序），不破坏量化核心写出的清单。"""
    wl = _wl(tmp_dir)
    wl.write_text(
        "watchlist:\n- ts_code: 600519.SH\n  name: 贵州茅台\n  market: A\n  note: 等回调\n",
        encoding="utf-8",
    )
    rc = mw.main(["--watchlist", str(wl), "set", "600519", "--BS", "B"])
    assert rc == 0
    text = wl.read_text(encoding="utf-8")
    # 条目恒顶格 + 子键恒 2 空格（PyYAML safe_dump 同风格）
    assert '\n- ts_code: "600519.SH"' in text
    assert '\n  name: "贵州茅台"' in text
    items = wb.load_watchlist(wl)
    assert items[0].name == "贵州茅台"


def test_quantify_save_watchlist_roundtrip(tmp_dir):
    """核心回归：量化核心 save_watchlist（PyYAML safe_dump，2 空格缩进）写出的清单，
    两个消费方都能读全字段——修复前旧正则 ^\\s{4} 只认 4 空格，2 空格文件会静默丢字段。"""
    from quantify.analysis.watchlist import WatchItem, save_watchlist

    wl = _wl(tmp_dir)
    save_watchlist(
        [WatchItem(ts_code="600519.SH", name="贵州茅台", market="A", note="等回调")], wl
    )
    # PyYAML 写出即标准 2 空格风格
    text = wl.read_text(encoding="utf-8")
    assert "\n- ts_code: 600519.SH" in text
    assert "\n  name: 贵州茅台" in text
    # 两个消费方（w-bottom / momentum 的极简解析）读全四字段
    for mod in (wb, mom):
        items = mod.load_watchlist(wl)
        assert len(items) == 1
        assert items[0].ts_code == "600519.SH"
        assert items[0].name == "贵州茅台"
        assert items[0].market == "A"
        assert items[0].note == "等回调"
    # 委托 set 加 param 后消费方仍能读全
    mw.main(["--watchlist", str(wl), "set", "600519", "--BS", "B"])
    for mod in (wb, mom):
        items = mod.load_watchlist(wl)
        assert items[0].name == "贵州茅台"


def test_legacy_4space_file_readable_by_consumers(tmp_dir):
    """向后兼容：旧 4 空格手编格式（早期模板产出）在消费方宽容正则下依然可读。"""
    wl = _wl(tmp_dir)
    wl.write_text(
        'watchlist:\n  - ts_code: "600519.SH"\n    name: "贵州茅台"\n    market: A\n    note: "等回调"\n',
        encoding="utf-8",
    )
    for mod in (wb, mom):
        items = mod.load_watchlist(wl)
        assert len(items) == 1
        assert items[0].name == "贵州茅台"
        assert items[0].market == "A"
    # 本脚本读宽容，重写后规范化为 2 空格标准格式
    mw.main(["--watchlist", str(wl), "set", "600519", "--BS", "B"])
    text = wl.read_text(encoding="utf-8")
    assert '\n- ts_code: "600519.SH"' in text
    assert "\n  BS: B" in text


def test_value_with_special_chars_roundtrip(tmp_dir):
    wl = _wl(tmp_dir)
    note = "等回调到 1500（含空格/括号）"
    mw.main(["--watchlist", str(wl), "add", "600519", "--note", note])
    _, items = mw.parse_text(wl.read_text(encoding="utf-8"))
    assert items[0]["note"] == note
    # 消费方读出的 note 与写入一致
    assert wb.load_watchlist(wl)[0].note == note


def test_header_comments_preserved(tmp_dir):
    wl = _wl(tmp_dir)
    wl.write_text(
        "# 我自己的手写头注释\nwatchlist:\n  - ts_code: \"600519.SH\"\n    name: 贵州茅台\n",
        encoding="utf-8",
    )
    mw.main(["--watchlist", str(wl), "set", "600519", "--BS", "B"])
    text = wl.read_text(encoding="utf-8")
    assert "# 我自己的手写头注释" in text
    assert "watchlist:" in text
