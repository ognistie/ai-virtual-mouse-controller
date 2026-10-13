"""
avmc
====

Permite rodar o projeto como modulo: `py -m avmc` (Windows) ou
`python -m avmc`. Funciona mesmo quando a pasta Scripts do Python nao esta
no PATH e o comando `avmc` ainda nao e' reconhecido.
"""

import sys

from main import main

if __name__ == "__main__":
    sys.exit(main())
