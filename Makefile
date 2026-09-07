PYTHON ?= python3
SHOPMATE_REPO ?= ../shopmate
SHOPMATE_PYTHON ?= $(SHOPMATE_REPO)/.venv/bin/python

.PHONY: test check-core-boundary check-shopmate-host check ownership-ablation shopmate-ownership-ablation

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

check-core-boundary:
	@files="$$(find src/stateeval/core -type f -name '*.py' -print)"; \
	if [ -z "$$files" ]; then \
		echo "Core package has no Python sources." >&2; \
		exit 2; \
	fi; \
	grep -n -i -E 'citybuddy|shopmate|(^|[^[:alnum:]_])(select|insert|update|delete)([^[:alnum:]_]|$$)' $$files; \
	status=$$?; \
	case $$status in \
		0) exit 1 ;; \
		1) exit 0 ;; \
		*) exit $$status ;; \
	esac

check-shopmate-host:
	@test -x "$(SHOPMATE_PYTHON)" || { echo "Install ShopMate dependencies with uv sync --frozen first." >&2; exit 2; }
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "$(SHOPMATE_PYTHON)" -B -m pytest tests/shopmate_host_cases.py -q -p no:cacheprovider

check: check-core-boundary test check-shopmate-host
	$(PYTHON) -m compileall -q src tests
	bash -n scripts/run_citybuddy_ownership_ablation.sh
	bash -n scripts/run_citybuddy_session_propagation_campaign.sh
	bash -n scripts/run_shopmate_ownership_ablation.sh
	git diff --check

ownership-ablation:
	./scripts/run_citybuddy_ownership_ablation.sh

shopmate-ownership-ablation:
	./scripts/run_shopmate_ownership_ablation.sh --output "$(OUTPUT)" $(ARGS)
