# -*- coding: utf-8 -*-
"""LightRAG 演示 —— Crucible 融合验证第 1 步。

用 mae 项目的原始文档做实体提取与三类查询验证:
  - LLM: DeepSeek (app-state.json 配置, OpenAI 兼容)
  - 嵌入: 本地 BAAI/bge-small-zh-v1.5 (512 维, hf-mirror 下载)
  - 文档: raw/sources/er.txt (冒烟) 与 mae.md (正式)

用法: .venv/bin/python scripts/lightrag_demo.py [--full]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# tiktoken 分词器文件预置在本地缓存 (Azure blob 直连不稳定)
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path.home() / ".cache" / "tiktoken"))

import numpy as np
from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import openai_complete_if_cache
from lightrag.utils import EmbeddingFunc

APP_STATE = Path.home() / ".py-llm-wiki" / "app-state.json"
PROJECT = Path.home() / "Desktop" / "AI" / "test" / "mae"
WORK_DIR = str(PROJECT / ".lightrag-mae")

EMBED_MODEL = "BAAI/bge-small-zh-v1.5"
EMBED_DIM = 512


def load_llm_config() -> dict:
    state = json.loads(APP_STATE.read_text(encoding="utf-8"))
    return state.get("llmConfig", {})


async def make_rag(llm_cfg: dict) -> LightRAG:
    async def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs):
        return await openai_complete_if_cache(
            llm_cfg.get("model") or "deepseek-chat",
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            api_key=llm_cfg.get("apiKey", ""),
            base_url=(llm_cfg.get("customEndpoint") or "https://api.deepseek.com").rstrip("/"),
            **kwargs,
        )

    # 本地嵌入模型 (懒加载, 避免导入开销)
    from sentence_transformers import SentenceTransformer

    st_model = SentenceTransformer(EMBED_MODEL)

    async def embedding_func(texts: list[str]) -> np.ndarray:
        return st_model.encode(texts, normalize_embeddings=True)

    rag = LightRAG(
        working_dir=WORK_DIR,
        llm_model_func=llm_model_func,
        embedding_func=EmbeddingFunc(
            embedding_dim=EMBED_DIM, max_token_size=512, func=embedding_func
        ),
    )
    await rag.initialize_storages()
    return rag


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="额外摄入 mae.md (23KB)")
    args = parser.parse_args()

    llm_cfg = load_llm_config()
    print(f"LLM: {llm_cfg.get('model')} @ {llm_cfg.get('customEndpoint')}")
    rag = await make_rag(llm_cfg)

    # 冒烟: er.txt (326B)
    er = (PROJECT / "raw" / "sources" / "er.txt").read_text(encoding="utf-8")
    await rag.ainsert(er)
    print("er.txt 摄入完成")

    # Q2 机制型查询 (hybrid)
    print("\n=== Q2 机制型 ===")
    r = await rag.aquery(
        "MAE 的 hiro 总线是什么？ER 和 IR 接口有什么区别？",
        param=QueryParam(mode="hybrid", enable_rerank=False),
    )
    print("回答:", str(r)[:400])

    # Q1 枚举型查询 (local)
    print("\n=== Q1 枚举型 ===")
    r = await rag.aquery(
        "列出文档中提到的所有组件、接口和概念实体",
        param=QueryParam(mode="local", only_need_context=True, enable_rerank=False),
    )
    print("上下文:", str(r)[:500])

    if args.full:
        mae = (PROJECT / "raw" / "sources" / "mae.md").read_text(encoding="utf-8")
        await rag.ainsert(mae)
        print("\nmae.md 摄入完成")
        r = await rag.aquery(
            "MAE 的系统架构分为哪几层？",
            param=QueryParam(mode="hybrid", enable_rerank=False),
        )
        print("分层查询:", str(r)[:300])


if __name__ == "__main__":
    asyncio.run(main())
