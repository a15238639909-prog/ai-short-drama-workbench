# -*- coding: utf-8 -*-
"""人设图资产库：漫画三视图 / 画册角色设定图 统一镜像到 人设图\。

用户确认的规则：
- 文件夹内只有 .png + 同名 .txt，没有子文件夹；
- 只收录：漫画「角色三视图」、画册「角色设定图」；
- 项目内原图保留，本模块只做资产镜像（做法 A）；
- 重新生成不覆盖：同名文件自动 _v2/_v3 递增；
- TXT = 人设原文 + 图片提示词原文 + 生成信息。
"""
from __future__ import annotations

import datetime
import pathlib
import re
import shutil

ASSET_ROOT = pathlib.Path(__file__).resolve().parent / "人设图"


def _safe(text, limit=60):
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(text or "")).strip(" ._")
    return (cleaned[:limit] or "未命名")


def _next_version_path(base: pathlib.Path):
    """同名存在时返回 _v2/_v3…，绝不覆盖，也不建子文件夹。"""
    if not base.exists():
        return base
    for n in range(2, 10000):
        cand = base.with_name(base.stem + "_v%d%s" % (n, base.suffix))
        if not cand.exists():
            return cand
    raise RuntimeError("资产文件名版本已用尽")


def store_character_asset(kind, project_name, character_name, image_path,
                          human_text, prompt_text, meta=None):
    """把一张人设三视图镜像到 人设图\，并写同名 TXT。

    kind: 漫画 / 画册
    project_name: 来源项目名（系列名或画册名）
    character_name: 人物名/主题名
    image_path: 项目内已生成的原图绝对路径
    human_text: 人设原文
    prompt_text: 最终图片提示词原文
    meta: 可选 {width,height,seed,preset}
    返回 (image_abs, txt_abs)；失败抛异常由调用方记录。
    """
    src = pathlib.Path(image_path)
    if not src.is_file():
        raise FileNotFoundError("人设资产原图不存在：%s" % src)
    ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    base_name = "%s_%s_%s.png" % (_safe(kind), _safe(project_name), _safe(character_name))
    dst_img = _next_version_path(ASSET_ROOT / base_name)
    shutil.copy2(str(src), str(dst_img))
    dst_txt = dst_img.with_suffix(".txt")
    lines = ["【人设原文】", str(human_text or "").strip(), "",
             "【图片提示词】", str(prompt_text or "").strip()]
    meta = meta or {}
    info = [
        "来源：%s · %s · %s" % (_safe(kind), _safe(project_name), _safe(character_name)),
        "类型：%s" % (meta.get("preset") or ("三视图" if kind == "漫画" else "角色设定图")),
    ]
    if meta.get("width"):
        info.append("尺寸：%s×%s" % (meta["width"], meta["height"]))
    if meta.get("seed"):
        info.append("seed：%s" % meta["seed"])
    info.append("时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines += ["", "【生成信息】"] + info
    dst_txt.write_text("\n".join(lines), encoding="utf-8")
    return str(dst_img), str(dst_txt)
