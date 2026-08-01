# Gestalt — common tasks. Run `make help` for the list.
.PHONY: help install uninstall lint test check check-py check-shell check-js \
        check-pill-js check-vendored-path-all check-repo sync-signers pack clean

EXT := src/extension/gestalt@asuramaya

# The family's shared recipe layer (sutra.mk, vendored like code under its
# own .version/.commit anchor — see docs/BOOTSTRAP.md and the file's own
# header). Supplies check-sutra (integrity+freshness for the three vendored
# .py modules), SUTRA_ROOT_ROWS (the canonical tracked-files row count used
# below in check-repo), and check-vendored-path (the checkout-run resolution
# guard). PILL must be set before the include; everything else in sutra.mk
# resolves relative to its own vendored location, not this Makefile's.
PILL := gestalt
include src/share/gestalt/lib/sutra.mk

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

lint: check-py check-shell

test:
	python3 tests/test_config.py
	python3 tests/test_endpoint.py
	python3 tests/test_resolve.py

# sutra.mk's check-sutra covers only the three vendored .py modules —
# BOOTSTRAP.md's own escape hatch for a pill that also vendors pill.js
# ("extend the for mod in... line") never made it into sutra.mk's
# generalized form (same gap RAMstein documents). Kept here as a small
# pill-side supplement, same integrity+freshness shape as check-sutra.
# pill.js is vendored but NOT YET wired into extension.js — hygiene only,
# no extension behavior change this pass.
check-pill-js:
	@f="$(EXT)/pill.js"; ver="$${f%.js}.version"; cmt="$${f%.js}.commit"; \
	sha=$$(awk '{print $$NF}' "$$ver"); \
	actual=$$(sha256sum "$$f" | cut -d' ' -f1); \
	if [ "$$sha" != "$$actual" ]; then \
	    echo "check-pill-js FAIL: $$f doesn't match $$ver" \
	         "(hand-edited? re-vendor: bash ~/code/REPOS/sutra/vendor.sh src/share/gestalt/lib $(EXT) --bootstrap=gestalt)"; \
	    exit 1; \
	fi; \
	echo "check-pill-js: integrity ok ($$f, sha256 $$sha)"; \
	canon="$$HOME/code/REPOS/sutra"; \
	if [ -d "$$canon/.git" ]; then \
	    if [ ! -f "$$cmt" ]; then \
	        echo "check-pill-js: freshness unknown ($$f has no .commit anchor, an older vendor)"; \
	    else \
	        recorded=$$(cat "$$cmt"); \
	        filehead=$$(git -C "$$canon" log -1 --format=%H -- pill.js); \
	        if git -C "$$canon" merge-base --is-ancestor "$$filehead" "$$recorded" 2>/dev/null; then \
	            echo "check-pill-js: freshness ok (vendored from $$recorded, at or after its own head $$filehead)"; \
	        elif git -C "$$canon" merge-base --is-ancestor "$$recorded" "$$filehead" 2>/dev/null; then \
	            echo "check-pill-js: LAG (vendored from $$recorded, canonical has since moved to" \
	                 "$$filehead) -- warn, not a failure"; \
	        else \
	            echo "check-pill-js FAIL: DRIFT ($$f's vendored commit $$recorded is not in" \
	                 "canonical's history at $$canon) -- re-vendor"; \
	            exit 1; \
	        fi; \
	    fi; \
	else \
	    echo "check-pill-js: canonical sutra checkout not present, freshness skipped"; \
	fi

# sutra.mk's check-vendored-path validates one SUTRA_CHECK_BIN per
# invocation by actually running it. gestaltctl is safe to run for real (a
# refused socket connect, no hardware touched) so it gets sutra.mk's own
# default recipe below with an explicit safe arg. gestaltd is NOT wired in
# here: it has no --help/no-op invocation today, only Daemon().start() —
# running it for real would open the camera and grab uinput, which has no
# place in a static check. Left as a follow-up (needs a real --help flag on
# gestaltd first, a CLI-surface change outside this pass's hygiene-only
# scope) rather than silently skipped — see docs/ARCHITECTURE.md's
# exemptions table.
check-vendored-path-all:
	@$(MAKE) check-vendored-path SUTRA_CHECK_BIN=src/bin/gestaltctl SUTRA_CHECK_ARGS=status

# rebuild packaging/release-signing/allowed_signers from the canonical keys
# (see docs/RELEASE-SIGNING.md — do NOT run casually; arm ONLY in the same
# act as cutting a signed release, per the sequencing rule there)
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
	bash -n install.sh uninstall.sh packaging/sync-signers.sh
	shellcheck install.sh uninstall.sh packaging/sync-signers.sh

check-js:
	node --check "$(EXT)/extension.js"
	python3 -c "import json; json.load(open('$(EXT)/metadata.json'))"

# static checks, CI-equivalent. Family grammar: check.
check: check-py check-shell check-js check-sutra check-pill-js check-vendored-path-all check-repo
	@echo "all static checks passed"

# The family's structural gate (REPO-STANDARD.md §5), mechanical only: it
# cannot judge whether a document is any good, only that the shape it's
# supposed to have is actually there and nothing contradicts it. Adapted
# from RAMstein's check-repo (coldspot's original) to gestalt's own file
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
