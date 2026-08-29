PYTHON ?= python3

.PHONY: test check-core-boundary check baseline

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

check-core-boundary:
	@files="$$(find src/stateeval/core -type f -name '*.py' -print)"; \
	if [ -z "$$files" ]; then \
		echo "Core package has no Python sources." >&2; \
		exit 2; \
	fi; \
	grep -n -i -E 'citybuddy|(^|[^[:alnum:]_])(select|insert|update|delete)([^[:alnum:]_]|$$)' $$files; \
	status=$$?; \
	case $$status in \
		0) exit 1 ;; \
		1) exit 0 ;; \
		*) exit $$status ;; \
	esac

check: check-core-boundary test
	$(PYTHON) -m compileall -q src tests
	bash -n scripts/run_citybuddy_baseline.sh
	git diff --check

baseline:
	./scripts/run_citybuddy_baseline.sh
