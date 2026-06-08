"""Set repository-local Python interpreter defaults.

Python imports this module automatically when commands run from the repository
root. The only intentional side effect is disabling bytecode writes so tests and
local app runs do not create `__pycache__` or `.pyc` artifacts.
"""

import sys

sys.dont_write_bytecode = True
