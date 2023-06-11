from pathlib import Path
import re

import pdoc
import evosax


docs = Path("docs/")

def get_version():
    # Taken from https://github.com/RobertTLange/evosax/blob/af5c32271583672a42adef08f19cd535a9fa0d93/setup.py#L26-L33
    VERSIONFILE = "evosax/_version.py"
    verstrline = open(VERSIONFILE, "rt").read()
    VSRE = r"^__version__ = ['\"]([^'\"]*)['\"]"
    mo = re.search(VSRE, verstrline, re.M)
    if mo:
        verstr = mo.group(1)
    else:
        raise RuntimeError("Unable to find version string in %s." % (VERSIONFILE,))
    return verstr

def gen_doc():
    pdoc.render.configure(
        docformat="google",
        logo="https://arxaqapi.github.io/logo.png",
        template_directory=docs / "template",
        edit_url_map={"evosax": "https://github.com/RobertTLange/evosax/blob/main/evosax/ "},
        footer_text=f"evosax v{get_version()}"
    )

    # Generate documentation for the evosax package and its submodules
    pdoc.pdoc(
        "evosax",
        output_directory=docs
        )

if __name__ == "__main__":
    # Disable documentation for __all__ variables, creates submodules
    del evosax.__all__ 
    del evosax.strategies.__all__
    
    gen_doc()

# FIXME - move to docs/ folder