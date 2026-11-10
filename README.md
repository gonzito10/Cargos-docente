# Buscador de cargos docentes en profesorados (PBA y CABA)

Web estática + recolector automático. Se actualiza sola cada 3 días con GitHub Actions (gratis).

## Puesta en marcha (5 minutos)
1. Creá un repositorio en GitHub y subí el contenido de esta carpeta.
2. **Settings → Pages**: Source = `Deploy from a branch`, Branch = `main`, carpeta `/docs`.
3. **Settings → Actions → General → Workflow permissions**: `Read and write permissions`.
4. **Actions → "Actualizar convocatorias" → Run workflow** (primera carga de datos, tarda unos minutos).
5. Tu buscador queda en `https://TU-USUARIO.github.io/NOMBRE-REPO/`.

Desde ahí el workflow corre todos los días, pero solo actualiza cuando pasaron 3 días desde la última vez.

## Cómo funciona
- **Fuentes**: no existe un listado central. El recolector recorre las webs de los institutos
  (`isfdNN-bue.infd.edu.ar`, `isfdytNN-bue…`, `ensN-caba…`, `iesN-caba…`) y la página oficial de CABA
  ("Cobertura de cátedras"). Detecta PDFs de convocatorias/selecciones y lee su texto para sacar
  profesorado y fecha de cierre.
- **Secretarías de Asuntos Docentes (SAD)**: para blogs de SAD con tabla de comunicados (ej. La Matanza) se leen
  solo los que dicen "Convocatoria …" y aluden a un instituto superior. Cada PDF se descarga y se detecta el cargo
  convocado (jefe/a de área, preceptor/a, bibliotecario/a, docente por horas/módulos, etc.). Para sumar otro
  distrito, agregá su página en `sad` de `scraper/sources.json` (`{anio}` se reemplaza por el año actual).
- **Agregar institutos**: editá `scraper/sources.json` (`sitios_extra` o `paginas_html`).
- **Pruebas**: `python -m unittest discover -s tests`.

## Límites a tener en cuenta
- Institutos que publican solo en Facebook/Instagram o con otro formato de web no se detectan.
- La fecha de cierre se detecta leyendo el PDF y puede fallar: la app lo indica ("cierre no detectado").
- No se pudo probar contra los sitios reales desde el entorno de desarrollo (sin acceso a esos dominios);
  la primera corrida en GitHub es la prueba real. Si algún sitio no aparece, revisá el log del workflow.
