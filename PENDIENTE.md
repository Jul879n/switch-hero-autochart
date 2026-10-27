# Pendiente: prueba real de playlists con Spotify

Objetivo: al usar una playlist, que se encuentre el 100 % de las canciones.

## Hecho

- Las playlists usan coincidencia exacta (todos los tokens de título y artista, sin variantes, duración a ±5 s).
- Si la fuente elegida no tiene coincidencia, el diálogo prueba las otras dos (SoundCloud, YouTube, MP3.pm).
- Medición en el servidor con «Today's Top Hits» (50 pistas): 50 de 50 con respaldo entre fuentes.
  Por fuente sola: MP3.pm 38, SoundCloud 17, YouTube 49.
- Desplegado en producción (commit `2194837`).
- `cf-autotunnel` publica solo `jarayac.cl`; Switch Hero responde en https://switchhero.jarayac.cl.

## Falta

1. Iniciar sesión en Komodo y, en el Repo `switchhero` → Config → Environment, poner
   `PUBLIC_URL=https://switchhero.jarayac.cl`; luego Pull. Hoy sigue con el dominio antiguo
   (`switchhero.jarayacontreras.cl`), que ya no resuelve.
2. En la app de Spotify (developer.spotify.com/dashboard):
   - Registrar el Redirect URI `https://switchhero.jarayac.cl/api/spotify/callback`.
   - Agregar el correo de la cuenta de Spotify en User Management (máx. 5; el dueño necesita Premium).
3. Prueba end-to-end en la app: «Conectar Spotify» → cargar una playlist propia → «Generar todo» y revisar
   cuántas pistas quedan «sin coincidencia». Lo medido hasta ahora no pasó por la app ni por una cuenta real.

Las variables de entorno se cargan siempre desde Komodo (ver `CLAUDE.md`).
