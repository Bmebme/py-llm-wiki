# py-llm-wiki 运行指南

llm_wiki（Karpathy LLM Wiki 方法论）的 Python/FastAPI 重编译版 —— 与桌面版 19828 API 契约兼容的自我构建知识库。

## 环境要求

| 依赖 | 版本 |
|------|------|
| Python | ≥ 3.11（本项目在 3.12 验证通过） |
| Node.js | ≥ 18 + npm |
| 系统 | macOS / Linux |

## 快速开始（从零到页面）

### 1. 获取代码

```bash
git clone https://github.com/Bmebme/py-llm-wiki.git
cd py-llm-wiki
```

### 2. 安装后端

二选一（Makefile 自动检测：优先 `.venv`，否则用当前激活环境的 python）：

**方式 A：venv**

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

**方式 B：conda（推荐给 conda 用户）**

```bash
conda create -n py-llm-wiki python=3.12 -y
conda activate py-llm-wiki
pip install -e ".[dev]"
```

### 3. 安装前端

```bash
cd frontend && npm install && cd ..
```

### 4. 配置 LLM

两种方式任选：

- **页面配置**（推荐）：启动后在设置页选 Provider 为 custom，填 OpenAI 兼容端点与 API Key（如 DeepSeek：`https://api.deepseek.com`）
- **直接编辑**：`~/.py-llm-wiki/app-state.json` 的 `llmConfig` 字段

⚠️ 配置文件含 API Key，**不要提交进仓库**。

### 5. 启动

**后台模式（推荐，一条命令，不占终端）**：

```bash
make dev-bg       # 后端 + 前端都后台启动
make stop         # 全部停止
```

日志：`/tmp/py-llm-wiki-backend.log` / `/tmp/py-llm-wiki-frontend.log`

**前台模式（调试用，阻塞当前终端）**：

```bash
make backend      # 终端 1：后端 http://127.0.0.1:19828
make frontend     # 终端 2：前端 http://localhost:1420
```

conda 用户先 `conda activate py-llm-wiki`（若 `.venv` 不存在，Makefile 自动使用 conda 环境；也可显式 `make backend PY=python`）。

### 6. 打开主页面

浏览器访问 **http://localhost:1420**（前端把 `/api` 同源代理到 19828，只需访问这一个地址）。

## 验证与测试

```bash
make test                        # 单元测试（当前 180 passed）
make test-real-llm               # 真实 LLM 集成测试（会调用模型产生费用）
curl http://127.0.0.1:19828/api/v1/health    # 后端健康检查
```

## 调试

```bash
# 后端热重载
.venv/bin/uvicorn backend.main:app --reload --port 19828

# 断点调试：pytest 或 uvicorn 直接挂 IDE（纯 Python，无编译步骤）
```

代码结构：

| 目录 | 职责 |
|------|------|
| `backend/api/` | HTTP 路由（19828 契约） |
| `backend/search/` | 混合检索引擎（关键词 + 向量 + 一跳图） |
| `backend/ingest/` | 摄入管线（两步思维链、队列、Source Watch） |
| `backend/chat/` | Chat Agent（工具调用） |
| `backend/graph/` | 关联图（relevance 计算） |
| `backend/wiki/` | 页面/索引/日志/wikilink 处理 |

项目数据：每个 wiki 项目是一个目录（`wiki/` 页面 + `raw/` 原文 + `.llm-wiki/` 状态），项目注册表在 `~/.py-llm-wiki/app-state.json`。

## 常见问题

| 症状 | 处理 |
|------|------|
| 端口占用 | `lsof -i :19828` / `lsof -i :1420` 找到进程后 kill |
| 前端起不来 | 确认 `frontend/node_modules` 已安装（`npm install`） |
| 页面显示旧项目 | 注册表里有 `/tmp` 测试项目残留，在设置里新建/导入自己的项目 |
| 后端报错 | 检查 `~/.py-llm-wiki/app-state.json` 的 llmConfig 是否有效 |
| LLM 调用 504（WSL/公司网络） | 代理环境变量把 LLM 请求转发到公司代理所致。启动前 `export LLM_WIKI_NO_PROXY=1`（LLM 直连），或 `export NO_PROXY="api.deepseek.com,.deepseek.com,localhost,127.0.0.1"`（只对 LLM 域名绕过代理） |

## 容器化部署的内源清单

Dockerfile 构建只下载两类东西 (无 apt 阶段):

1. 基础镜像 `python:3.12-slim` (Docker Hub → 内网 registry 代理)
2. pip 包 (linux/amd64 轮子, 内网 PyPI 源按此清单缓存):

```
fastapi 0.141.1 / starlette 1.6.0 / annotated-doc / typing-inspection
uvicorn[standard] 0.52.4 / httptools 0.8.0 / watchfiles 1.2.0 /
  websockets 17.1 / python-dotenv 1.2.3 / uvloop 0.22.1 / click 8.5.0
httpx 0.28.1 / httpcore 1.0.9 / h11 / certifi / idna / anyio
pydantic 2.13.5 / pydantic_core 2.46.5 (Rust) / annotated-types / typing_extensions
pypdfium2 5.13.0 (C) / python-docx 1.2.0 / lxml 6.1.3 (C)
python-multipart / PyYAML / watchdog
```

平台相关轮子 (pydantic_core/lxml/httptools/uvloop/pypdfium2) 必须缓存
manylinux x86_64 版本。构建时:

```dockerfile
FROM <内网registry>/python:3.12-slim
ENV PIP_INDEX_URL=http://<内网pip源>/simple
```
