"""El motor de precios es el del SDK (`laplace.pricing`), y este módulo es ese mismo.

Vive en el SDK desde D-184 para que `laplace.guard` cuente el gasto con la misma tabla
y la misma cuenta que la ingesta. El coste de la traza se sigue calculando aquí, en la
ingesta (D-005): el SDK del usuario sólo lo usa para su límite local.

No se reexportan los nombres: se sustituye el módulo, para que `pricing._custom`,
`set_custom_prices` y los `monkeypatch` de las pruebas toquen el único estado que hay.
"""

import sys

from laplace import pricing as _motor

sys.modules[__name__] = _motor
