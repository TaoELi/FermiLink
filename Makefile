.PHONY: lint pretty doc html

SRC ?= src tests

lint:
	flake8 --select=F --per-file-ignores="**/__init__.py:F401" $(SRC)
	black --check $(SRC)

pretty:
	black $(SRC)

SPHINXBUILD ?= sphinx-build
SPHINXAPIDOC ?= sphinx-apidoc
DOCS_SOURCE = docs/source
DOCS_BUILD = docs/build/html
DOCS_API = $(DOCS_SOURCE)/api

doc:
	LC_ALL=C LANG=C FERMILINK_ROUTER_ONLY_IMPORT=1 $(SPHINXAPIDOC) -o $(DOCS_API) src/fermilink -f -e -M --remove-old src/fermilink/web/app.py src/fermilink/web/storage_helpers.py
	LC_ALL=C LANG=C FERMILINK_ROUTER_ONLY_IMPORT=1 $(SPHINXBUILD) -b html $(DOCS_SOURCE) $(DOCS_BUILD)

html:
	python -c 'import pathlib, webbrowser, sys; index = pathlib.Path("docs/build/html/index.html").resolve(); sys.exit("Docs not built; run `make doc` first.") if not index.exists() else webbrowser.open(index.as_uri())'
