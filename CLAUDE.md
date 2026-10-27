# CLAUDE.md

## Despliegue y variables de entorno

Producción se despliega con un stack de **Komodo** (https://komodo.jarayac.cl, stack `switchhero`, fuente Git:
`Jul879n/switch-hero-autochart`, rama `main`, `compose.yaml` de la raíz). Un push a `main` dispara el webhook.
Las variables de entorno (`SPOTIFY_*`, `SMTP_*`, `ADMIN_*`, `INSTALLER_TOKEN`, `PUBLIC_URL`) se cargan siempre
desde Komodo (stack → Environment), no a mano en el VPS. Nunca escribir valores secretos en el repositorio,
en la memoria ni en este archivo.
