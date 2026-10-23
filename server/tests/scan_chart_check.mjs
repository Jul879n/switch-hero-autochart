// Uso: node scan_chart_check.mjs <carpeta-de-cancion>
// Valida con scan-chart (parser compatible con Clone Hero). Paquete: scan-chart (npm).
import { scanChartFolder } from 'scan-chart'
import fs from 'node:fs'
const dir = process.argv[2]
if (!dir) { console.error('Falta la carpeta'); process.exit(2) }
const files = fs.readdirSync(dir).map(name => ({ fileName: name, data: fs.readFileSync(`${dir}/${name}`) }))
const r = scanChartFolder(files)
console.log(JSON.stringify({ instruments: r.chartMetadata?.tracks?.map(t => `${t.instrument}/${t.difficulty}:${t.noteCount}`), issues: r.chartIssues ?? r.folderIssues }, null, 1))
