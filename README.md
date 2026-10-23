# Switch Hero Autochart

Genera cartas jugables para [Switch Hero](https://github.com/R-drg/switch-hero) desde cualquier canción
(archivo o URL): Guitarra, Co-op, Rítmica, Bajo, Teclado y Batería, en Easy/Medium/Hard/Expert.

- `server/`: API FastAPI + web (cuentas propias; solo el admin crea o invita usuarios) + pipeline `autochart`.
- `nro/`: homebrew «SH Installer» (libnx + libcurl) que instala las canciones en la SD.
- `scripts/push_ftp.py`: respaldo, sube un ZIP o carpeta a la Switch por FTP.

## Variables de entorno del servidor
| Variable | Uso |
|---|---|
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Crea (o promueve) al administrador al arrancar |
| `PUBLIC_URL` | URL pública, para los enlaces de invitación |
| `INSTALLER_TOKEN` | Token Bearer que usa el `.nro` en `/api/switch/*` |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | Envío de correo (invitaciones y avisos) |
| `YTDLP_COOKIES` | Cookies de yt-dlp (por defecto `/data/cookies.txt`) |

## Pruebas
`cd server && pytest` (las de tempo con audio requieren librosa).

## .nro
Compilar: `DEVKITPRO=/opt/devkitpro cmake -S nro -B nro/build -G Ninja -DCMAKE_TOOLCHAIN_FILE=$DEVKITPRO/cmake/Switch.cmake && cmake --build nro/build`.
Config en la SD: `sdmc:/switch/sh-installer/config.ini` con `server=https://…` y `token=…`.
