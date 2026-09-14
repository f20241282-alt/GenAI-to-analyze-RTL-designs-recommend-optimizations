# GenRTL -- convenience targets. Everything is also reachable via
#   python3 -m genrtl <command>
.PHONY: help demo suggest check baseline paths transforms small full report dashboard test test-fast clean

BENCH ?= full
NAME  ?= $(BENCH)_run
ITERS ?= 16
LLM   ?=

help:
	@echo "make demo           toolchain-free advisor demo (no Yosys/OpenSTA)"
	@echo "make suggest PATH=x  suggest optimisations for any RTL file/dir"
	@echo "make check          verify the toolchain and libraries"
	@echo "make baseline       synthesise + analyse (BENCH=small|full)"
	@echo "make paths          show critical paths and their RTL slices"
	@echo "make transforms     print the transformation catalogue"
	@echo "make small          closed-loop run on the fast configuration"
	@echo "make full           closed-loop run on the ~50K-cell benchmark"
	@echo "make report NAME=x  rebuild reports from an existing run"
	@echo "make dashboard      launch the Streamlit dashboard"
	@echo "make test           full self-check (includes SAT proofs, slow)"
	@echo "make test-fast      self-check without the SAT proofs"

PATH_ARG ?= rtl

demo:
	python3 demo.py

suggest:
	python3 -m genrtl suggest $(PATH_ARG)

check:
	python3 -m genrtl check

baseline:
	python3 -m genrtl --bench $(BENCH) baseline

paths:
	python3 -m genrtl --bench $(BENCH) paths --top 5 --slice

transforms:
	python3 -m genrtl transforms --scan

small:
	python3 -m genrtl --bench small optimize --name small_run --iters 10 $(if $(LLM),--llm $(LLM),)

full:
	python3 -m genrtl --bench full optimize --name full_run --iters $(ITERS) $(if $(LLM),--llm $(LLM),)

report:
	python3 -m genrtl report --name $(NAME)

dashboard:
	streamlit run dashboard/app.py

test:
	python3 tests/run_all.py

test-fast:
	python3 tests/run_all.py --fast

clean:
	rm -rf runs/*/candidate runs/*/iter_* runs/*/final runs/*/final_formal
