---
paths:
  - "apps/web/**"
---

# La web (`apps/web`)

- Textos en los cinco idiomas a la vez (`apps/web/lib/mensajes/*.ts`); las pruebas exigen
  las mismas claves en todos.
- Estilos en `apps/web/app/estilos/`, una hoja por pantalla. El color sale sólo de los
  tokens de `00-temas.css`; `test_tema.py` mide el contraste en los cuatro temas.
- El dinero se formatea sólo en `lib/format.ts`.
- Comprobar: `cd apps/web && npx tsc --noEmit`. Después de tocarla,
  `.venv/Scripts/python.exe scripts/build_ui.py`: las pruebas de pantalla prueban
  `apps/web/out`.
