# CLAUDE.md

## Despliegue y variables de entorno

Producción (VPS, stack en `/opt/stacks/switchhero`) se despliega con el recurso **Repo `switchhero`** de Komodo
(https://komodo.jarayac.cl/repos): un push a `main` dispara el webhook, Komodo hace pull y ejecuta
`docker compose -p switchhero up -d --build`. El volumen de datos es `switchhero_switchhero-data`.

Las variables de entorno (`SPOTIFY_*`, `SMTP_*`, `ADMIN_*`, `INSTALLER_TOKEN`, `PUBLIC_URL`) se cargan siempre desde
Komodo (Repo `switchhero` → Config → Environment; se escriben en `/opt/stacks/switchhero/.env`), nunca a mano en el
VPS: Komodo sobrescribe ese archivo en cada pull, así que una variable que no esté en Environment se pierde.
Nunca escribir valores secretos en el repositorio, en la memoria ni en este archivo.
