# py-llm-wiki daemon 容器化 (crucible 部署的一环, docs 见 Crucible 仓库 deploy.md)
# 约定:
#   PY_LLM_WIKI_DATA_DIR=/data       状态/app-state 持久化卷
#   /projects                         项目目录挂载点 (容器内项目路径 = /projects/<name>)
#   LLM_WIKI_LLM_*                    统一 LLM 环境注入 (覆盖 app-state 的 llmConfig)
FROM python:3.12-slim

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN pip install --no-cache-dir \
        fastapi 'uvicorn[standard]' httpx pydantic \
        python-multipart pyyaml pypdfium2 python-docx watchdog

WORKDIR /srv
COPY backend ./backend
COPY pyproject.toml ./

ENV PY_LLM_WIKI_HOST=0.0.0.0
ENV PY_LLM_WIKI_DATA_DIR=/data
VOLUME /data /projects

EXPOSE 19828
CMD ["python", "-m", "backend.main"]
