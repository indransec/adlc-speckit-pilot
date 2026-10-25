# Travel Claims Assistant - ADLC simulation
export PYTHONPATH := $(CURDIR):$(CURDIR)/agents/orchestrator/src
UV ?= uv
RELEASE ?= $(shell sed -n 's/^release: *//p' system.yaml)
PORT ?= 8000

.PHONY: help setup data datasets api ui phoenix dev test evals evals-quick perf traceability validate lifecycle simulate status clean

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

setup:           ## install Python 3.12 deps (uv) and generate synthetic data
	$(UV) sync
	$(MAKE) data

data:            ## reset data/ : SQLite, PDFs in the object store, FAISS policy index, demo claims
	$(UV) run python -m synthetic.generate

datasets:        ## regenerate versioned eval datasets (golden + adversarial)
	$(UV) run python -m synthetic.generate --datasets

api:             ## FastAPI backend on :8000 (docs at /docs)
	$(UV) run uvicorn backend.app:app --port $(PORT) --reload --reload-dir backend --reload-dir shared --reload-dir agents --reload-dir tools

ui:              ## Streamlit front end on :8501
	cd frontend && $(UV) run streamlit run Home.py

phoenix:         ## Arize Phoenix trace UI + OTLP collector on :6006
	PHOENIX_WORKING_DIR=$(CURDIR)/data/phoenix $(UV) run phoenix serve

dev:             ## phoenix + api + ui together (Ctrl-C stops all)
	@trap 'kill 0' INT TERM; $(MAKE) phoenix & sleep 8; $(MAKE) api & sleep 6; $(MAKE) ui; wait

test:            ## unit + routing tests (writes evidence/<release>/test-report.json)
	$(UV) run python scripts/run_tests.py $(RELEASE)

validate:        ## G2 manifest validation
	$(UV) run python scripts/validate_manifests.py

evals:           ## G3/G4 evals in an isolated data dir (live models if OPENROUTER_API_KEY is set)
	$(UV) run python evals/run_evals.py $(RELEASE)

evals-quick:     ## half the golden set, no counterfactual fairness
	$(UV) run python evals/run_evals.py $(RELEASE) --quick

perf:            ## performance test at 3x peak
	$(UV) run python evals/perf_test.py $(RELEASE)

traceability:    ## REQ -> verification -> evidence matrix
	$(UV) run python scripts/build_traceability.py $(RELEASE)

status:          ## gate overview
	$(UV) run python scripts/adlc.py status

simulate:        ## walk the whole ADLC G0 -> G7 with simulated approvers
	$(UV) run python scripts/adlc.py simulate

clean:           ## remove runtime data (keeps evidence and datasets)
	rm -rf data .pytest_cache
