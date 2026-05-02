# QDaily archive pipeline.
#
# Local end-to-end build:
#   make inventory   # parse source/ into data/articles.jsonl
#   make fetch       # download Wayback HTML to cache/  (long; resumable)
#   make extract     # cache/ -> data/articles_extracted.jsonl
#   make render      # data/articles_extracted.jsonl -> public/
#   make serve       # local preview at http://localhost:8765
#
# Or do everything in one go:
#   make all

PY := .venv/bin/python
YEAR ?= 2014

.PHONY: venv inventory fetch extract render serve all clean-public

venv:
	test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt

inventory: venv
	$(PY) tools/inventory.py --year $(YEAR)

fetch: venv
	$(PY) tools/fetch_wayback.py --rate 1.0

extract: venv
	$(PY) tools/extract.py

render: venv
	$(PY) tools/render.py --base-url "/" --image-mode wayback

serve: venv
	$(PY) -m http.server 8765 --directory public

all: inventory fetch extract render

clean-public:
	rm -rf public
