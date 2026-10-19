# Visor SAR 3D – App Streamlit

Visor del modelo conceptual del **Sistema Acuífero Raigón (San José)**: formaciones en 3D, pozos coloreados por
formación, cortes y fichas de pozo, sobre imagen satelital, mapa de calles o topográfico, con la posibilidad de
agregar capas propias (shapefile, GeoPackage, GeoJSON o servicios WMS).

## Requisitos

- Windows con **Python 3.12 o 3.13** (de [python.org](https://www.python.org/downloads/)). Al instalarlo,
  marcá **"Add python.exe to PATH"**.
- Conexión a internet para los fondos de mapa (satélite, OSM, topográfico, relieve) y los WMS.
  El modelo y los pozos funcionan sin internet.

> El Python que trae QGIS (OSGeo4W) **no sirve** para esto: usá un Python instalado aparte.

## Opción rápida (doble clic)

1. Descomprimí la carpeta `app_SAR_streamlit` donde quieras (por ejemplo en Documentos).
2. Doble clic en **`iniciar_app.bat`**.
   La primera vez crea un entorno (`.venv`) e instala las librerías (5 a 10 minutos). Las siguientes, abre directo.
3. Se abre el navegador en `http://localhost:8501`. Para cerrar la app, cerrá la ventana negra.

## Opción manual (consola)

Abrí una consola (PowerShell o "Símbolo del sistema") **dentro de la carpeta** de la app:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Las veces siguientes alcanza con:

```
.venv\Scripts\activate
streamlit run app.py
```

## Cómo se usa

**Barra lateral**
- **Fondo:** imagen satelital (Esri), calles (OSM), topográfico (Esri) o sin fondo, más relieve sombreado.
  Por defecto el mapa se apoya **sobre el relieve real y tapa la litología interpolada** (el modelo es una
  interpolación de pozos, el mapa es el dato observado). Con la opacidad del mapa o los recortes se ve el modelo debajo.
  Si se destilda «Mapa sobre el modelo», el mapa vuelve a un plano debajo del modelo.
- **Hidrogeología:** superficie piezométrica regional, piezometría de la red de monitoreo (jun-2024) y techo de la
  1ª napa con aporte, como hojas de color en 3D, con sus puntos de dato.
- **Formaciones:** prender/apagar Suelo/Actual, Dolores, Libertad, Chuy, Raigón, Camacho y Fray Bentos; opacidad
  (bajala para ver los pozos dentro del modelo).
- **Pozos:** tramos coloreados por formación, nombres, grosor, y filtros por fuente, profundidad mínima y formaciones
  atravesadas.
- **Vista:** exageración vertical y recortes desde el norte y desde el este (para ver el interior del modelo).
- **Capas propias:**
  - Archivo: shapefile **comprimido en .zip** (con .shp, .dbf, .shx y **.prj**), GeoPackage o GeoJSON.
    Se reproyecta solo a WGS84. Podés colorear por un campo y dibujarla en el plano del mapa o sobre el modelo.
  - WMS: URL del servicio + nombre de la capa (parámetro LAYERS), por ejemplo los servicios de la IDE Uruguay
    o de DINAMIGE. El nombre de la capa figura en el GetCapabilities del servicio o en QGIS.

**Pestañas**
- **Mapa 3D:** arrastrar = mover · Ctrl + arrastrar (o clic derecho) = rotar e inclinar · rueda = zoom.
  Pasá el mouse para ver los datos; **clic en un pozo** para abrir su ficha.
- **Corte:** predefinidos (A-A', B-B'), entre dos pozos o por coordenadas UTM; pozos proyectados dentro de una
  distancia a elección; descarga en PNG.
- **Pozo:** columna litológica, tabla de tramos y descarga en CSV (del pozo o de todos los pozos filtrados).
- **Hidrogeología:** mapas de piezometría, profundidad al agua, espesor saturado, zona semiconfinada, techo de la
  napa y transmisividad estimada; dirección de flujo; hidrogramas y tendencias de la red de monitoreo; descargas en
  PNG y CSV. En la pestaña **Corte** se puede superponer el nivel piezométrico.
- **Acerca del modelo:** método y limitaciones.

## Actualizar el modelo con una base nueva

Reemplazá `entrada/base_acotada.xlsx` (misma estructura que la base acotada: hojas *Pozos* y *Litología*) y corré:

```
.venv\Scripts\activate
python preparar_datos.py
```

o `python preparar_datos.py ruta\a\otra_base.xlsx`. Regenera la carpeta `data/` en unos segundos; después recargá la
app en el navegador (tecla R o el menú ⋮ → Rerun).

## Contenido

| Archivo / carpeta | Qué es |
|---|---|
| `app.py` | La app |
| `iniciar_app.bat` | Arranque con doble clic (Windows) |
| `preparar_datos.py` | Regenera `data/` desde la base |
| `modelo_sar/` | Extracción de contactos, modelo 2.5D por formación y capas hidrogeológicas (`hidro.py`, `datos_hidro.py`) |
| `entrada/base_acotada.xlsx` | Base de pozos acotada (15/10/2026) |
| `data/` | Datos precalculados: grilla del modelo, capas hidrogeológicas (`hidro.npz`), pozos, tramos, contactos, monitoreo, outliers y controles |
| `static/terrain-worker.js` | Decodificador de relieve de loaders.gl (MIT) servido por la app, para no depender de unpkg.com |
| `.streamlit/config.toml` | Habilita la carpeta `static/` |
| `requirements.txt` | Librerías con versiones fijas |

## Limitaciones del modelo

- **Camacho:** la mayoría de los pozos terminan dentro; su espesor es un *mínimo* donde no se atraviesa.
- **Fray Bentos:** ningún pozo llega a su base; se dibuja una lámina de 30 m.
- **10 pozos excluidos** por cota de boca dudosa (más de 15 m de diferencia con la topografía del modelo):
  UK000499, UK000750, UK001167, UK001170, UK001241, UK001242, UK001305, UK001398, UK001450, UK001796.
- Las celdas a más de 3 km de un pozo con contacto quedan fuera del modelo. Celda de 500 m.
- **Piezometría regional:** mezcla niveles de distintas fechas (la mayoría sin fecha). La red de monitoreo muestra un
  descenso mediano de ~5 cm/año, chico frente a los ~40 m de relieve piezométrico, pero localmente puede haber
  diferencias de varios metros. La superficie de monitoreo es de una sola fecha pero con solo 29 pozos.
- **Transmisividad:** estimada por capacidad específica (Logan); solo orden de magnitud.
- Relieve para apoyar el mapa: Terrarium (Mapzen, AWS Open Data). Fondos de mapa: © Esri, Maxar, Earthstar Geographics; © colaboradores de OpenStreetMap.
