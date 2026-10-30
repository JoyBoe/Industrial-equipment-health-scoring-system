ROOT := $(CURDIR)
VENV := $(ROOT)/.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn

.PHONY: help venv install dev run test test-all test-frontend coverage seed seed-data train evaluate frontend frontend-install frontend-build frontend-lint clean distclean

help:
	@echo "venv     Create the virtualenv"
	@echo "install  Install backend dependencies"
	@echo "dev      Run the API with autoreload"
	@echo "run      Run the API without autoreload"
	@echo "test         Run the backend test suite"
	@echo "test-frontend Run the frontend test suite"
	@echo "test-all     Run both suites"
	@echo "coverage     Report frontend test coverage"
	@echo "seed        Seed equipment master data"
	@echo "seed-data   Seed synthetic sensor telemetry"
	@echo "train       Train the anomaly detector"
	@echo "evaluate    Measure detector precision and recall"
	@echo "frontend          Run the dashboard dev server"
	@echo "frontend-install  Install frontend dependencies"
	@echo "frontend-build    Build the dashboard for production"
	@echo "frontend-lint     Lint the dashboard source"
	@echo "clean    Remove caches and the local database"
	@echo "distclean  Also discard the trained model (forces a retrain)"

venv:
	python3 -m venv $(VENV)

install:
	"$(PIP)" install -r backend/requirements.txt

dev:
	cd backend && "$(UVICORN)" app.main:app --reload --host 0.0.0.0 --port 8000

run:
	cd backend && "$(UVICORN)" app.main:app --host 0.0.0.0 --port 8000

test:
	"$(PY)" -m pytest

test-frontend:
	cd frontend && npm run test

test-all: test test-frontend

coverage:
	cd frontend && npm run test:coverage

seed:
	cd backend && "$(PY)" -m app.seed

seed-data:
	cd backend && "$(PY)" -m app.seed_telemetry --replace

train:
	cd backend && "$(PY)" -m app.ml.train

evaluate:
	cd backend && "$(PY)" -m app.ml.evaluate

frontend-install:
	cd frontend && npm install

frontend:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-lint:
	cd frontend && npx eslint src --ext .js,.jsx

clean:
	rm -rf .pytest_cache frontend/coverage
	find backend tests -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -f backend/test_staging.db data/health_scoring.db data/health_scoring.db-wal data/health_scoring.db-shm
	rm -rf frontend/dist
	@echo "Rebuild with: make seed && make seed-data && make train"

# Also discards the trained detector, forcing a full retrain. The model is
# reproducible from the seed scripts, so it is a build artifact, not source.
distclean: clean
	rm -f models/anomaly_detector.joblib models/anomaly_detector.meta.json
	@echo "Model removed. Run 'make train' to rebuild it."

