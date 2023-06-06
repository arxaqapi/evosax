# https://pdoc.dev/docs/pdoc.html
doc: c
	pdoc --docformat google \
		 --logo "http://0.0.0.0:8000/logo.png" \
		 --output-dir docs/ \
		 --template-directory docs/template \
		 --edit-url evosax=https://github.com/RobertTLange/evosax/blob/main/evosax/ \
  		 --footer-text "evosax v.0.1.4" \
		 evosax

serve:
	cd docs && python -m http.server


c clean:
	rm -f docs/*.html
	rm -f docs/*.js
	rm -rf docs/evosax