.PHONY: dev backend frontend test test-real-llm typecheck dev-bg backend-bg frontend-bg stop

# Python 解释器自动检测：优先 .venv，否则用当前激活环境（conda 等）的 python
PY ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python)

# ---- 前台模式（调试用，阻塞当前终端） ----
backend:
	$(PY) -m backend.main

frontend:
	cd frontend && npm run dev

# ---- 后台模式（日常使用，日志落 /tmp） ----
backend-bg:
	@nohup $(PY) -m backend.main > /tmp/py-llm-wiki-backend.log 2>&1 & \
	echo "后端已后台启动: http://127.0.0.1:19828 （日志 /tmp/py-llm-wiki-backend.log）"

frontend-bg:
	@cd frontend && nohup npm run dev > /tmp/py-llm-wiki-frontend.log 2>&1 & \
	echo "前端已后台启动: http://localhost:1420 （日志 /tmp/py-llm-wiki-frontend.log）"

dev-bg: backend-bg frontend-bg

stop:
	@pkill -f "backend.main" 2>/dev/null; pkill -f "vite" 2>/dev/null; \
	echo "已停止后端与前端"

test:
	$(PY) -m pytest backend/tests -q

test-real-llm:
	LLM_WIKI_REAL_LLM=1 $(PY) -m pytest backend/tests -m real_llm -q

typecheck:
	cd frontend && npm run typecheck
