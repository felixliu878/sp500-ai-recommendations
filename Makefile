PYTHON ?= python3

.PHONY: install test pilot-dry pilot-live full-live

install:
	$(PYTHON) -m pip install -e .

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

pilot-dry:
	PYTHONPATH=src $(PYTHON) -m sp500_ai.cli pilot --backend dry-run --repetitions 3

pilot-live:
	PYTHONPATH=src $(PYTHON) -m sp500_ai.cli pilot --backend openai --repetitions 1

full-live:
	PYTHONPATH=src $(PYTHON) -m sp500_ai.cli full --backend openai --repetitions 1
