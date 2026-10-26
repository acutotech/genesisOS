SHELL := /bin/bash
.DEFAULT_GOAL := help

# Shell sources: scripts, stage scripts, and the sourced config files.
SH_FILES := $(shell find . -path ./pi-gen -prune -o -path ./build -prune -o -path ./.git -prune -o \
              \( -name '*.sh' -o -name 'config' -o -name 'custom.conf' -o -name 'genesis-helper' \
              -o -name 'genesis-session' -o -name 'genesis-session-start' -o -name 'genesis-respawn' -o -name 'genesis-fallback' -o -name 'genesis-python' -o -name 'genesis-firstboot' \
              -o -name 'genesis-shell' -o -name 'genesis-shell-ctl' -o -name 'genesis-settings' \) -type f -print | sort)
PY_FILES := $(shell find . -path ./pi-gen -prune -o -path ./build -prune -o -path ./.git -prune -o \
              \( -name '*.py' -o -name 'genesis-xdg-autostart' -o -name 'genesis-idle' \) -type f -print | sort)
YAML_FILES := $(shell find . -path ./pi-gen -prune -o -path ./build -prune -o -path ./.git -prune -o \
              \( -name '*.yml' -o -name '*.yaml' -o -name '.yamllint' \) -type f -print | sort)

.PHONY: help lint lint-shell lint-yaml lint-actions test build build-native clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

lint: lint-shell lint-yaml lint-actions ## Run all linters

lint-shell: ## shellcheck every shell file, byte-compile every Python file
	shellcheck -s bash -x -P SCRIPTDIR $(SH_FILES)
	python3 -m py_compile $(PY_FILES) && find . -path ./pi-gen -prune -o -name __pycache__ -type d -print0 | xargs -0r rm -rf
	python3 scripts/check-shell-imports.py

lint-yaml: ## yamllint workflow and config files
	yamllint -s $(YAML_FILES)

lint-actions: ## actionlint the GitHub workflow (skipped if actionlint is not installed)
	@if command -v actionlint >/dev/null 2>&1; then actionlint -color; \
	else echo "actionlint not installed, skipping (https://github.com/rhysd/actionlint)"; fi

test: ## Dry-run the custom stage and first-boot setup; unit-test Genesis Shell logic and Genesis Imager (no root, no Docker)
	scripts/test-stage.sh
	scripts/test-firstboot.sh
	python3 scripts/test-shell-logic.py
	python3 -m unittest discover -s imager -v

build: ## Build the image with Docker (see scripts/build-local.sh --help)
	scripts/build-local.sh

build-native: ## Build the image natively (Debian/Raspberry Pi OS host, needs sudo)
	scripts/build-local.sh --native

clean: ## Remove local build outputs and pi-gen's Docker container (keeps the pi-gen checkout)
	rm -rf build deploy pi-gen/work pi-gen/deploy
	-docker rm -v pigen_work 2>/dev/null
