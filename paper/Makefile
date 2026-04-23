# Build an EMNLP/ACL submission PDF.
#
# One-time setup:
#   1. Download acl.sty and acl_natbib.bst into this directory from:
#        https://github.com/acl-org/acl-style-files
#      (or copy them from an ACL template overleaf project).
#   2. Ensure a TeX distribution is installed (TeX Live or MacTeX).
#
# Targets:
#   make          -- build main.pdf (default)
#   make watch    -- rebuild on every save (needs latexmk)
#   make clean    -- remove build artifacts but keep the PDF
#   make distclean-- remove everything including the PDF
#   make wordcount-- rough word count via texcount (needs texcount)

MAIN = main
SECTIONS := $(wildcard sections/*.tex)
BIB = references.bib

.PHONY: all watch clean distclean wordcount check-style

all: $(MAIN).pdf

$(MAIN).pdf: $(MAIN).tex $(SECTIONS) $(BIB) check-style
	pdflatex -interaction=nonstopmode $(MAIN).tex
	bibtex $(MAIN) || true
	pdflatex -interaction=nonstopmode $(MAIN).tex
	pdflatex -interaction=nonstopmode $(MAIN).tex

check-style:
	@test -f acl.sty || { \
		echo "ERROR: acl.sty not found in $(CURDIR)."; \
		echo "Download it from https://github.com/acl-org/acl-style-files"; \
		echo "and place acl.sty and acl_natbib.bst alongside main.tex."; \
		exit 1; \
	}

watch:
	latexmk -pdf -pvc -interaction=nonstopmode $(MAIN).tex

wordcount:
	texcount -inc -sum $(MAIN).tex

clean:
	rm -f $(MAIN).aux $(MAIN).bbl $(MAIN).blg $(MAIN).log $(MAIN).out \
	      $(MAIN).toc $(MAIN).fls $(MAIN).fdb_latexmk $(MAIN).synctex.gz
	rm -f sections/*.aux

distclean: clean
	rm -f $(MAIN).pdf
