# Gestalt — common tasks. Run `make help` for the list.
EXT := extension/gestalt@asuramaya

.PHONY: help install uninstall lint test check pack clean

help:
	@echo "Gestalt targets:"
	@echo "  make install    install daemon (uv venv) + user service + pill"
	@echo "  make uninstall  remove everything"
	@echo "  make check      run all static checks (CI-equivalent)"
	@echo "  make lint       ruff + shellcheck"
	@echo "  make test       config-sanitize fuzz (hardware-free)"
	@echo "  make pack       build the extensions.gnome.org zip"
	@echo "  make clean      remove build artifacts"

install:
	./install.sh

uninstall:
	./uninstall.sh

lint:
	ruff check .
	shellcheck install.sh uninstall.sh

test:
	python3 tests/test_config.py

check: lint
	python3 -m py_compile bin/gestaltd bin/gestaltctl \
		$$(find gestalt providers -name '*.py')
	python3 tests/test_config.py
	node --check $(EXT)/extension.js
	python3 -c "import json; json.load(open('$(EXT)/metadata.json'))"
	@echo "all static checks passed"

pack:
	cd $(EXT) && zip -r ../../dist/gestalt@asuramaya.shell-extension.zip metadata.json extension.js

clean:
	rm -rf dist __pycache__ */__pycache__ */*/__pycache__ .ruff_cache
