"""数据腿注册表（阶段 2 W2.2）——外部数据源的唯一定义处。

背景：改造前 gutMGene 的本机绝对路径硬编码在 4 个模块默认值里（interact:174/
hypothesize:433/subgraph:1047/deepchain:342），V01 快照/kg 主图/文献库同理
——换机器要改代码，路径错了半路才崩（tool_line_review §一病根之二）。

规则（stage2_spec §二）：
1. 注册表=configs/data_sources.json（入 git：sha256/版本/许可档位/仓内相对路径）；
2. 本机绝对路径（Nas 等）只存在于 configs/local/data_sources_local.json
   （gitignore 覆盖件），按 local_key 引用——公开仓不带内部布局；
3. **启动即校验锚 sha256，不符立刻退出**（fail-closed，防"文件被换/版本漂移"）；
4. 解析顺序与疾病档案同式：显式命令行 > 注册表 > 模块原默认；
5. KG_ROOT 环境变量 > 自动探测仓根（spec §四，与 cli.py 同序）。
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

_AUTO_ROOT = Path(__file__).resolve().parents[2]


def get_root() -> Path:
    """KG_ROOT > 自动探测（editable 安装下两者相同）。"""
    env = os.environ.get("KG_ROOT", "")
    return Path(env) if env else _AUTO_ROOT


ROOT = get_root()
REGISTRY_PATH = ROOT / "configs" / "data_sources.json"
LOCAL_PATH = ROOT / "configs" / "local" / "data_sources_local.json"


def _load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_path(entry: dict) -> Path:
    """条目 → 实际路径：local_key 走本地覆盖件，否则 ROOT 锚定相对路径。"""
    if "local_key" in entry:
        if not LOCAL_PATH.exists():
            sys.exit(f"[data_sources] 本机条目 {entry['local_key']} 需要 "
                     f"{LOCAL_PATH}（gitignore 覆盖件）——内容一行示例：{{'gutmgene_dir': '/path/to/gutMGene'}}")
        local = _load_json(LOCAL_PATH)
        if entry["local_key"] not in local:
            sys.exit(f"[data_sources] 本地覆盖件缺键 {entry['local_key']} "
                     f"（现有键: {sorted(local)}）")
        return Path(local[entry["local_key"]])
    return ROOT / entry["path"]


def verify(key: str, entry: dict) -> Path:
    """锚校验+取路径——不符即退出（启动即败，W2.2 验收硬指标）。"""
    p = resolve_path(entry)
    anchor = p / entry["anchor_file"] if "anchor_file" in entry else p
    if not anchor.exists():
        sys.exit(f"[data_sources] 腿 {key} 锚文件缺失: {anchor}")
    actual = _sha256(anchor)
    if actual != entry["anchor_sha256"]:
        sys.exit(f"[data_sources] 腿 {key} 锚 sha 不符（启动即败）: {anchor}\n"
                 f"  期望 {entry['anchor_sha256'][:16]}… 实测 {actual[:16]}…"
                 f"（版本漂移或文件被换——核对注册表条目 {key}）")
    return p


def resolve(args: argparse.Namespace, mapping: dict, orig_defaults: dict):
    """按"显式 > 注册表 > 原默认"填 None 位；注册表路径经过锚校验。

    mapping：dest → (注册表键, 是否列表型)——列表型如 --k3-batch（append）。
    只校验被本模块实际用到的腿（按 mapping 出现的键），不全校验。
    """
    reg = _load_json(REGISTRY_PATH)
    for dest, (key, is_list) in mapping.items():
        if not hasattr(args, dest):
            continue
        cur = getattr(args, dest)
        if cur is not None:
            continue  # 显式传参最高优先（显式路径不校验=用户自担，沿旧行为）
        if key in reg:
            val = verify(key, reg[key])  # Path（校验通过后返回）
            setattr(args, dest, [str(val)] if is_list else val)
        else:
            val = orig_defaults[dest]
            # 原默认登记为字符串（与 argparse type=Path 对齐：非列表回填 Path）
            setattr(args, dest,
                    [str(v) for v in val] if is_list else Path(val))
    return args
