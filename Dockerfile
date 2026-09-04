# py-llm-wiki 全栈镜像: 后端 daemon + 浏览器版 UI (同源托管)
# 内网部署约定见 RUN.md (Docker 启动一节)
# 构建: docker build -t py-llm-wiki .
# 内源: FROM 指向内网 registry; PIP_INDEX_URL / npm registry 指内源
FROM node:20-slim AS ui
WORKDIR /srv
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install --registry=https://registry.npmmirror.com
COPY frontend ./
RUN npm run build

FROM python:3.12-slim

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN pip install --no-cache-dir \
        fastapi 'uvicorn[standard]' httpx pydantic \
        python-multipart pyyaml pypdfium2 python-docx watchdog

WORKDIR /srv
COPY backend ./backend
COPY pyproject.toml ./
COPY --from=ui /srv/dist ./frontend/dist

ENV PY_LLM_WIKI_HOST=0.0.0.0
ENV PY_LLM_WIKI_DATA_DIR=/data
VOLUME /data /projects

EXPOSE 19828
CMD ["python", "-m", "backend.main"]
