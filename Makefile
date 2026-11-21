# 一键任务。CI（ubuntu）走这里；Windows 本地没有 make 就用 tasks.ps1。
PY ?= .venv/Scripts/python.exe
ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
else
PY := .venv/bin/python
endif

.PHONY: help setup models demo test report clean

help:
	@echo "make setup    创建 venv 并装依赖"
	@echo "make models   拉取 panda 模型"
	@echo "make demo     跑一次 pick->place，出 reports/d1_pick_place.png"
	@echo "make test     跑全量离线测试（不需要 API key）"
	@echo "make report   生成 reports/latest.md"

setup:
	python -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[dev]"

models:
	powershell -ExecutionPolicy Bypass -File scripts/fetch_models.ps1

demo:
	$(PY) scripts/demo_d1.py

test:
	$(PY) -m pytest -m "not live" -q

report:
	$(PY) scripts/run_suite.py --out reports/latest.md

clean:
	rm -rf .pytest_cache reports/*.md
