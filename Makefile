.PHONY: dev backend frontend test test-real-llm typecheck

# Python 解释器自动检测：优先 .venv，否则用当前激活环境（conda 等）的 python
PY ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python)

backend:
	$(PY) -m backend.main

frontend:
	cd frontend && npm run dev

test:
	$(PY) -m pytest backend/tests -q

test-real-llm:
	LLM_WIKI_REAL_LLM=1 $(PY) -m pytest backend/tests -m real_llm -q

typecheck:
	cd frontend && npm run typecheck
