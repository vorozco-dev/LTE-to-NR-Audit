import os
import sys

# Carpeta del .exe como raíz de db/ y output/ (si no se definió ROOT_DIRECTORY)
os.environ.setdefault("ROOT_DIRECTORY", os.path.dirname(sys.executable))
