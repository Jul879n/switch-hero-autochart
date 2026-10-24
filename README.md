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

## .nro («SH Installer»)
Compilar: `DEVKITPRO=/opt/devkitpro cmake -S nro -B nro/build -G Ninja -DCMAKE_TOOLCHAIN_FILE=$DEVKITPRO/cmake/Switch.cmake && cmake --build nro/build`.
Copiar `nro/build/sh-installer.nro` a `sdmc:/switch/`. Al abrirlo por primera vez muestra un QR: escanéelo con el teléfono,
inicie sesión en la web y pulse «Vincular». La sesión queda en `sdmc:/switch/sh-installer/config.ini`
(`server=` es opcional; por defecto `https://switchhero.jarayacontreras.cl`). Desde la web (también desde el teléfono),
«Enviar a la Switch» pone la canción en cola y la app la instala sola mientras esté abierta (consulta cada 10 s).
`INSTALLER_TOKEN` sigue valiendo como `token=` fijo (ve todas las canciones).
