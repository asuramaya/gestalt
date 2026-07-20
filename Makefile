# Gestalt — common tasks. Run `make help` for the list.
EXT := extension/gestalt@asuramaya

.PHONY: help install uninstall lint test check check-sutra pack clean

help:
	@echo "Gestalt targets:"
	@echo "  make install    install daemon (uv venv) + user service + pill"
	@echo "  make uninstall  remove everything"
	@echo "  make check      run all static checks (CI-equivalent)"
	@echo "  make lint       ruff + shellcheck"
	@echo "  make test       hardware-free tests (config fuzz + endpoint predictor)"
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
	python3 tests/test_endpoint.py
	python3 tests/test_resolve.py

# drift guard for the vendored sutra copy: integrity (hash matches what
# vendor.sh recorded — the copy wasn't hand-edited) always runs; freshness
# (diff against the canonical source) only when that checkout is present,
# which it normally isn't in CI.
check-sutra:
	@sha=$$(awk '{print $$NF}' bin/sutra.version); \
	actual=$$(sha256sum bin/sutra.py | cut -d' ' -f1); \
	if [ "$$sha" != "$$actual" ]; then \
	    echo "check-sutra FAIL: bin/sutra.py doesn't match bin/sutra.version" \
	         "(hand-edited? re-vendor: bash ~/code/REPOS/sutra/vendor.sh bin)"; \
	    exit 1; \
	fi; \
	echo "check-sutra: integrity ok"; \
	canon="$$HOME/code/REPOS/sutra/sutra.py"; \
	if [ -f "$$canon" ]; then \
	    if cmp -s bin/sutra.py "$$canon"; then \
	        echo "check-sutra: freshness ok (matches canonical)"; \
	    else \
	        echo "check-sutra FAIL: bin/sutra.py differs from canonical $$canon (re-vendor)"; \
	        exit 1; \
	    fi; \
	fi

check: lint check-sutra
	python3 -m py_compile bin/gestaltd bin/gestaltctl \
		$$(find gestalt providers mcp -name '*.py')
	python3 tests/test_config.py
	python3 tests/test_endpoint.py
	python3 tests/test_resolve.py
	node --check $(EXT)/extension.js
	python3 -c "import json; json.load(open('$(EXT)/metadata.json'))"
	@echo "all static checks passed"

pack:
	cd $(EXT) && zip -r ../../dist/gestalt@asuramaya.shell-extension.zip metadata.json extension.js

clean:
	rm -rf dist __pycache__ */__pycache__ */*/__pycache__ .ruff_cache
