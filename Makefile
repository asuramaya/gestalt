# Gestalt — common tasks. Run `make help` for the list.
.PHONY: help install uninstall lint test check check-py check-shell check-js \
        check-repo sync-signers pill pack clean

EXT := src/extension/gestalt@asuramaya

# The family's shared recipe layer (sutra.mk, vendored like code under its
# own .version/.commit anchor — see docs/BOOTSTRAP.md and the file's own
# header). Supplies check-sutra (integrity+freshness, including pill.js when
# SUTRA_EXT_DIR is set — 0.12.9 folds what used to be gestalt's own hand-
# rolled check-pill-js target), SUTRA_ROOT_ROWS (the canonical tracked-files
# row count used below in check-repo), and check-vendored-path[-all] (the
# checkout-run resolution guard; SUTRA_CHECK_BINS is the native form of what
# used to be a second hand-rolled target here — both pilot supplements
# deleted on this re-vendor, per the thread that predicted this the day they
# were written: threads/2cd969c4). PILL, SUTRA_EXT_DIR and SUTRA_CHECK_BINS
# must all be set before the include; everything else in sutra.mk resolves
# relative to its own vendored location, not this Makefile's.
PILL := gestalt
SUTRA_EXT_DIR := $(EXT)
# gestaltctl is safe to run for real (a refused socket connect, no hardware
# touched); gestaltd is NOT wired in here: it has no --help/no-op invocation
# today, only Daemon().start() -- running it for real would open the camera
# and grab uinput, which has no place in a static check. Left as a follow-up
# (needs a real --help flag on gestaltd first) rather than silently skipped
# -- see docs/ARCHITECTURE.md's exemptions table.
SUTRA_CHECK_BINS := src/bin/gestaltctl
SUTRA_CHECK_ARGS := status
include src/share/gestalt/lib/sutra.mk

help:
	@echo "Gestalt targets:"
	@echo "  make install    install daemon (uv venv) + user service + pill"
	@echo "  make uninstall  remove everything"
	@echo "  make pill       (re)stage the GNOME extension only — no venv rebuild"
	@echo "  make check      run all static checks (CI-equivalent)"
	@echo "  make lint       ruff + shellcheck"
	@echo "  make test       hardware-free tests (config fuzz + endpoint predictor)"
	@echo "  make pack       build the extensions.gnome.org zip"
	@echo "  make clean      remove build artifacts"

install:
	./install.sh

uninstall:
	./uninstall.sh

# gestalt is the only pill where the extension is installed by install.sh
# rather than as its own separate root-vs-user split (ramstein/coldspot/
# byebyte/phanspeed's `make pill` exists because their daemon install is
# root and the pill install can't be, so it never runs there) — gestalt's
# whole install is already a per-user step, so this is a genuine re-stage
# shortcut rather than a load-bearing separate path: same script install.sh
# itself calls, so the two can't drift into two different "how to install
# the pill" answers. Never needs sudo.
pill:
	bash packaging/install-pill.sh .

lint: check-py check-shell

test:
	python3 tests/test_config.py
	python3 tests/test_endpoint.py
	python3 tests/test_resolve.py

# rebuild packaging/release-signing/allowed_signers from the canonical keys
# (see docs/RELEASE-SIGNING.md — do NOT run casually; arm strictly BEFORE
# tagging, never after, per the sequencing rule there and docs/RELEASING.md)
sync-signers:
	bash packaging/sync-signers.sh

check-py: test
	python3 -m py_compile src/bin/gestaltd src/bin/gestaltctl \
		src/share/gestalt/lib/sutra.py src/share/gestalt/lib/sutra_update.py src/share/gestalt/lib/sutra_xen.py \
		$$(find src/gestalt src/providers src/mcp src/research -name '*.py')
	# src/share/gestalt/lib/ is vendored, byte-identical, never gestalt's own
	# style to enforce — check-sutra covers ITS integrity separately.
	ruff check --line-length 100 --target-version py38 \
		--select E,F,W,I,UP,B src/bin src/gestalt src/providers src/mcp src/research

# shellcheck --rcfile only exists from 0.11.0 and ubuntu-latest rejects the
# flag, so exclusions are passed inline instead of via a config file (no
# shellcheckrc, anywhere — REPO-STANDARD.md §3). None excluded today.
check-shell:
	bash -n install.sh uninstall.sh packaging/sync-signers.sh packaging/install-pill.sh
	shellcheck install.sh uninstall.sh packaging/sync-signers.sh packaging/install-pill.sh

check-js:
	node --check "$(EXT)/extension.js"
	python3 -c "import json; json.load(open('$(EXT)/metadata.json'))"

# static checks, CI-equivalent. Family grammar: check.
check: check-py check-shell check-js check-sutra check-vendored-path-all check-repo
	@echo "all static checks passed"

# The family's structural gate (REPO-STANDARD.md §5), mechanical only: it
# cannot judge whether a document is any good, only that the shape it's
# supposed to have is actually there and nothing contradicts it. Adapted
# from ramstein's check-repo (coldspot's original) to gestalt's own file
# list and its Wave-C exemptions.
check-repo:
	@fail=0; \
	for f in README.md LICENSE Makefile install.sh uninstall.sh .gitignore .gitattributes \
	         docs/USAGE.md docs/ARCHITECTURE.md; do \
	    if [ ! -e "$$f" ]; then echo "check-repo FAIL: missing $$f"; fail=1; fi; \
	done; \
	if [ ! -e docs/RELEASING.md ] && ! grep -q 'docs/RELEASING.md' docs/ARCHITECTURE.md 2>/dev/null; then \
	    echo "check-repo FAIL: no docs/RELEASING.md and no exemption for it"; fail=1; \
	fi; \
	if [ ! -e src/data/man/gestalt.1 ] && ! grep -q 'src/data/man/gestalt.1' docs/ARCHITECTURE.md 2>/dev/null; then \
	    echo "check-repo FAIL: no src/data/man/gestalt.1 and no exemption for it"; fail=1; \
	fi; \
	rows=$(SUTRA_ROOT_ROWS); \
	if [ "$$rows" -gt 12 ]; then \
	    echo "check-repo FAIL: root has $$rows rows, standard caps it at 12"; fail=1; \
	else \
	    echo "check-repo: root row count ok ($$rows)"; \
	fi; \
	if ! grep -q '^## Map' README.md 2>/dev/null; then \
	    echo "check-repo FAIL: README.md has no navigation block (## Map)"; fail=1; \
	fi; \
	for h in Troubleshooting "Repo Layout"; do \
	    if grep -q "^## $$h" README.md 2>/dev/null; then \
	        echo "check-repo FAIL: README.md carries a post-install heading ('$$h') that belongs in docs/USAGE.md"; fail=1; \
	    fi; \
	done; \
	if [ ! -f packaging/VERSION ]; then \
	    echo "check-repo FAIL: no packaging/VERSION"; fail=1; \
	fi; \
	if grep -rnE "VERSION[[:space:]]*=[[:space:]]*['\"][0-9]" \
	    src/bin/gestaltd src/bin/gestaltctl install.sh uninstall.sh \
	    "$(EXT)/extension.js" 2>/dev/null; then \
	    echo "check-repo FAIL: a literal version string exists outside packaging/VERSION"; fail=1; \
	fi; \
	if [ -f .github/workflows/release.yml ] && grep -v '^[[:space:]]*#' .github/workflows/release.yml 2>/dev/null | grep -q -- '--generate-notes'; then \
	    echo "check-repo FAIL: release.yml still uses --generate-notes, not --notes-file"; fail=1; \
	fi; \
	stray=$$(find docs -name '*.md' -not -path '*/.*' | while read -r f; do git ls-files --error-unmatch "$$f" >/dev/null 2>&1 || echo "$$f"; done); \
	if [ -n "$$stray" ]; then \
	    echo "check-repo FAIL: untracked *.md under docs/: $$stray"; fail=1; \
	fi; \
	spec=$$(find . -name '*-SPEC.md' -not -path './.git/*'); \
	if [ -n "$$spec" ]; then \
	    echo "check-repo FAIL: *-SPEC.md left in the repo (specs belong in the seat's office): $$spec"; fail=1; \
	fi; \
	if [ -f docs/ARCHITECTURE.md ] && grep -q '^## Standard exemptions' docs/ARCHITECTURE.md; then \
	    bad=$$(awk '/^## Standard exemptions/{f=1;next} f && /^\|/ && !/^\| *Item *\|/ && !/^\|---/{ n=gsub(/\|/,"|"); if (n<3) print }' docs/ARCHITECTURE.md); \
	    if [ -n "$$bad" ]; then echo "check-repo FAIL: exemptions table has a row missing a column"; fail=1; fi; \
	fi; \
	if [ "$$fail" -eq 0 ]; then echo "check-repo: all mechanical checks passed"; else exit 1; fi

pack:
	cd $(EXT) && zip -r ../../../dist/gestalt@asuramaya.shell-extension.zip metadata.json extension.js pill.js

clean:
	rm -rf dist __pycache__ $$(find . -name __pycache__) .ruff_cache
