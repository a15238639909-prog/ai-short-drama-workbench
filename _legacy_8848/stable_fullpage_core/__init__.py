# -*- coding: utf-8 -*-
"""stable_fullpage_core_v1：唯一的稳定整页漫画生产核心。
与 app.py 中 director_v2.3—v2.7 提示词分支完全隔离。"""
__version__ = "stable_fullpage_core_v1"

from . import contracts, models, planning, prompt_compiler, audit, storage, pipeline

__all__ = ["contracts", "models", "planning", "prompt_compiler",
           "audit", "storage", "pipeline"]
