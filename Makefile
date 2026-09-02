.PHONY: dev backend frontend test test-real-llm typecheck

backend:
	.venv/bin/python -m backend.main

frontend:
	cd frontend && npm run dev

test:
	.venv/bin/python -m pytest backend/tests -q

test-real-llm:
	LLM_WIKI_REAL_LLM=1 .venv/bin/python -m pytest backend/tests -m real_llm -q

typecheck:
	cd frontend && npm run typecheck
