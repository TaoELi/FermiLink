SRC ?= src tests

.PHONY: lint pretty

lint:
	flake8 --select=F --per-file-ignores="**/__init__.py:F401" $(SRC)
	black --check $(SRC)

pretty:
	black $(SRC)
