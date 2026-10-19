"""Visor 3D del modelo conceptual del Sistema Acuífero Raigón (San José) – DINAGUA.

Correr con:  streamlit run app.py
Los datos se leen de data/ (se regeneran con preparar_datos.py).
"""
import io, json, tempfile, urllib.parse, zipfile, hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

AQUI = Path(__file__).parent
DATA = AQUI / "data"
st.set_page_config(page_title="Visor SAR 3D", page_icon="💧", layout="wide")


class DeckCompacto(pdk.Deck):
    """pydeck serializa con sangría (indent=2): con miles de bloques eso duplica el tamaño que viaja al navegador."""
    def to_json(self):
        from pydeck.bindings.json_tools import default_serialize
        return json.dumps(self, sort_keys=True, default=default_serialize, separators=(",", ":"))


# ----------------------------------------------------------------------------- datos
@st.cache_data(show_spinner="Cargando modelo…")
def cargar():
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    g = np.load(DATA / "grilla.npz")
    grid = {k: g[k] for k in g.files}
    tr = pd.read_parquet(DATA / "tramos.parquet")
    po = pd.read_parquet(DATA / "pozos.parquet")
    return meta, grid, tr, po


@st.cache_data(show_spinner=False)
def cargar_hidro():
    """Capas hidrogeológicas (si preparar_datos.py las generó)."""
    if not (DATA / "hidro.npz").exists():
        return None
    h = np.load(DATA / "hidro.npz")
    return dict(capas={k: h[k] for k in h.files}, puntos=pd.read_parquet(DATA / "hidro_puntos.parquet"),
                mon=pd.read_parquet(DATA / "monitoreo.parquet"), tend=pd.read_csv(DATA / "tendencia_niveles.csv"))


SUPS_HIDRO = {  # clave -> (nombre, capa de puntos, unidad)
    "PIEZO": ("Superficie piezométrica regional (NE)", "NE regional", "m s.n.m."),
    "PIEZO_MON": ("Superficie piezométrica – monitoreo", "Monitoreo", "m s.n.m."),
    "NAPA_TOP": ("Techo de la 1ª napa con aporte", "Techo 1ª napa", "m s.n.m."),
}
MAPAS_HIDRO = {  # clave -> (título, cmap, unidad, vmin, vmax)
    "PIEZO": ("Superficie piezométrica regional", "Blues", "m s.n.m.", -5, 45),
    "PIEZO_MON": ("Superficie piezométrica – monitoreo", "Blues", "m s.n.m.", -5, 45),
    "PROF_AGUA": ("Profundidad al nivel del agua", "YlOrBr", "m bajo terreno", 0, 30),
    "SAT": ("Espesor saturado del acuífero (Chuy + Raigón)", "GnBu", "m", 0, 50),
    "CONF": ("Condición del acuífero (1 = semiconfinado, 0 = libre)", "coolwarm", "", 0, 1),
    "NAPA_TOP": ("Techo de la 1ª napa con aporte", "viridis", "m s.n.m.", None, None),
    "LOGT": ("Transmisividad estimada, log10 T (Logan)", "RdYlBu", "log10 m²/d", 0.5, 3),
}


def hex2rgb(h):
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


@st.cache_data(show_spinner=False)
def bloques_base():
    """Un registro por celda y formación con espesor > 0,2 m."""
    meta, grid, _, _ = cargar()
    SUP, MASK, LON, LAT, gx, gy = grid["SUP"], grid["MASK"], grid["LON"], grid["LAT"], grid["gx"], grid["gy"]
    nf = len(meta["formaciones"])
    filas = []
    ii, jj = np.where(~MASK)
    for i, j in zip(ii, jj):
        poly = [[round(float(LON[i, j]), 5), round(float(LAT[i, j]), 5)],
                [round(float(LON[i, j + 1]), 5), round(float(LAT[i, j + 1]), 5)],
                [round(float(LON[i + 1, j + 1]), 5), round(float(LAT[i + 1, j + 1]), 5)],
                [round(float(LON[i + 1, j]), 5), round(float(LAT[i + 1, j]), 5)]]
        for k in range(nf):
            top, bot = float(SUP[k, i, j]), float(SUP[k + 1, i, j])
            if top - bot > 0.2:
                filas.append((k, poly, top, bot, top - bot, gx[j], gy[i]))
    df = pd.DataFrame(filas, columns=["k", "poly", "top", "bot", "e", "X", "Y"])
    nombres = [f["nombre"] for f in meta["formaciones"]]
    df["tip"] = [f"<b>{nombres[k]}</b><br>Techo: {t:.1f} m s.n.m.<br>Base: {b:.1f} m s.n.m.<br>Espesor: {e:.1f} m"
                 for k, t, b, e in zip(df.k, df.top, df.bot, df.e)]
    return df


meta, grid, TR, PO = cargar()
HID = cargar_hidro()
FORMS = meta["formaciones"]
NF = len(FORMS)
COLF = [hex2rgb(f["color"]) for f in FORMS] + [hex2rgb(meta["basamento"]["color"])]
GRIS = hex2rgb(meta["gris"])
NOMF = [f["nombre"] for f in FORMS] + ["Basamento"]
Z_MIN = meta["z_min"]

# ----------------------------------------------------------------------------- estado
ss = st.session_state
ss.setdefault("capas", [])      # capas propias: dicts
ss.setdefault("pozo_sel", None)
ss.setdefault("archivos_vistos", set())

# ----------------------------------------------------------------------------- barra lateral
sb = st.sidebar
sb.title("Visor SAR 3D")
sb.caption("Sistema Acuífero Raigón · San José · cotas en m s.n.m.")

with sb.expander("Fondo", expanded=True):
    fondo = st.radio("Mapa base", ["Imagen satelital (Esri)", "Mapa de calles (OSM)", "Topográfico (Esri)", "Sin fondo"],
                     label_visibility="collapsed")
    relieve = st.checkbox("Relieve sombreado (Esri)")
    fondo_arriba = st.checkbox("Mapa sobre el modelo (apoyado en el relieve)", value=True,
                               help="El mapa se dibuja sobre la superficie del terreno, tapando la litología "
                                    "interpolada. Bajá la opacidad o usá los recortes para ver el modelo debajo. "
                                    "Si lo destildás, el mapa queda en un plano por debajo del modelo.")
    op_fondo = st.slider("Opacidad del mapa", 0.0, 1.0, 0.85, 0.05, disabled=not fondo_arriba)

if HID is not None:
    with sb.expander("Hidrogeología", expanded=False):
        sel_hid = {k: st.checkbox(v[0], value=False, key=f"h{k}") for k, v in SUPS_HIDRO.items()}
        op_hid = st.slider("Opacidad de las superficies", 0.1, 1.0, 0.75, 0.05)
        pts_hid = st.checkbox("Puntos de dato de las superficies elegidas", value=True)
        st.caption("Superficies sin bloques: hojas de color por cota. Detalle y mapas en la pestaña «Hidrogeología».")
else:
    sel_hid, op_hid, pts_hid = {}, 0.7, False

with sb.expander("Formaciones", expanded=True):
    sel_form = []
    for k, f in enumerate(FORMS):
        c1, c2 = st.columns([1, 9])
        c1.markdown(f"<div style='width:16px;height:16px;margin-top:9px;border-radius:3px;background:{f['color']};"
                    f"border:1px solid #0003'></div>", unsafe_allow_html=True)
        sel_form.append(c2.checkbox(f["nombre"], value=True, key=f"form{k}"))
    opacidad = st.slider("Opacidad", 0.1, 1.0, 0.6, 0.05, help="Bajala para ver los pozos dentro del modelo.")

with sb.expander("Pozos", expanded=True):
    ver_pozos = st.checkbox("Tramos coloreados por formación", value=True)
    ver_nombres = st.checkbox("Nombre de pozo", value=False)
    grosor = st.slider("Grosor de los pozos (m)", 40, 400, 120, 20)
    fuentes = sorted(PO.fuente.unique())
    f_fuente = st.multiselect("Fuente", fuentes, default=fuentes)
    f_prof = st.slider("Profundidad mínima (m)", 0, int(PO.prof_total.max()), 0)
    f_form = st.multiselect("Que atraviesen (alguna de):", NOMF, default=[], placeholder="Todas")

with sb.expander("Vista", expanded=True):
    ve = st.slider("Exageración vertical", 5, 200, 60, 5)
    cn = st.slider("Recortar desde el norte (%)", 0, 100, 0)
    ce = st.slider("Recortar desde el este (%)", 0, 100, 0)

# ---- capas propias
with sb.expander("Capas propias", expanded=False):
    st.caption("Shapefile en .zip (con .prj), GeoPackage o GeoJSON. Se reproyecta solo a WGS84.")
    up = st.file_uploader("Archivo", type=["zip", "geojson", "json", "gpkg"], label_visibility="collapsed")
    if up is not None:
        fid = hashlib.md5(up.getvalue()).hexdigest()
        if fid not in ss.archivos_vistos:
            try:
                import geopandas as gpd
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / up.name
                    p.write_bytes(up.getvalue())
                    if up.name.lower().endswith(".zip"):
                        with zipfile.ZipFile(p) as z:
                            z.extractall(td)
                        shp = [q for q in Path(td).rglob("*.shp")]
                        if not shp:
                            raise ValueError("el .zip no contiene un .shp")
                        gdf = gpd.read_file(shp[0])
                    else:
                        gdf = gpd.read_file(p)
                if gdf.crs is None:
                    st.warning("La capa no tiene sistema de coordenadas; se asume WGS84 (EPSG:4326).")
                    gdf = gdf.set_crs(4326)
                gdf = gdf.to_crs(4326)
                gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
                if len(gdf) > 5000:
                    gdf["geometry"] = gdf.geometry.simplify(0.0002)
                campos = [c for c in gdf.columns if c != "geometry"]
                ss.capas.append(dict(tipo="vector", nombre=up.name, gdf=gdf, campos=campos,
                                     campo=next((c for c in campos if not pd.api.types.is_numeric_dtype(gdf[c])), None),
                                     visible=True, opacidad=0.6, arriba=False))
                ss.archivos_vistos.add(fid)
            except Exception as e:  # noqa
                st.error(f"No se pudo leer la capa: {e}")
    st.markdown("**Servicio WMS** (IDE Uruguay, DINAMIGE, etc.)")
    w_url = st.text_input("URL del servicio", placeholder="https://.../wms")
    w_capa = st.text_input("Nombre de la capa (LAYERS)")
    if st.button("Agregar WMS"):
        if w_url and w_capa:
            ss.capas.append(dict(tipo="wms", nombre=f"WMS: {w_capa}", url=w_url.strip(), capa=w_capa.strip(),
                                 visible=True, opacidad=0.8))
        else:
            st.warning("Completá la URL y el nombre de la capa.")
    for n, c in enumerate(list(ss.capas)):
        st.divider()
        a, b = st.columns([3, 2])
        c["visible"] = a.checkbox(c["nombre"], value=c["visible"], key=f"cv{n}{c['nombre']}")
        if b.button("Quitar", key=f"cx{n}{c['nombre']}", help="Quitar capa", type="tertiary"):
            ss.capas.remove(c)
            st.rerun()
        c["opacidad"] = st.slider("Opacidad", 0.1, 1.0, c["opacidad"], 0.05, key=f"co{n}{c['nombre']}")
        if c["tipo"] == "vector" and c["campos"]:
            ops = ["(un solo color)"] + c["campos"]
            sel = st.selectbox("Colorear por", ops, index=ops.index(c["campo"]) if c["campo"] in ops else 0,
                               key=f"cc{n}{c['nombre']}")
            c["campo"] = None if sel == ops[0] else sel
            c["arriba"] = st.checkbox("Dibujar sobre el modelo (si no, queda en el plano del mapa)",
                                      value=c.get("arriba", False), key=f"ca{n}{c['nombre']}")


# ----------------------------------------------------------------------------- filtros
lon_all, lat_all = grid["LON"], grid["LAT"]
lat_cut = lat_all.max() - (lat_all.max() - lat_all.min()) * cn / 100
lon_cut = lon_all.max() - (lon_all.max() - lon_all.min()) * ce / 100

pozos_ok = PO[PO.fuente.isin(f_fuente) & (PO.prof_total >= f_prof)]
if f_form:
    pozos_ok = pozos_ok[pozos_ok.formaciones.apply(lambda s: any(f in s for f in
                        [FORMS[i]["id"] if i < NF else "Basamento" for i, n in enumerate(NOMF) if n in f_form]))]
pozos_ok = pozos_ok[(pozos_ok.lat <= lat_cut) & (pozos_ok.lon <= lon_cut)]


def Z(z):
    return (np.asarray(z) - Z_MIN) * ve


# ----------------------------------------------------------------------------- estilo del mapa base
FUENTES = {
    "Imagen satelital (Esri)": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                                "Imagen © Esri, Maxar, Earthstar Geographics"),
    "Mapa de calles (OSM)": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "© colaboradores de OpenStreetMap"),
    "Topográfico (Esri)": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", "© Esri"),
}
HILLSHADE = "https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}"
# relieve para apoyar el mapa: Terrarium (Mapzen / AWS Open Data), cota = R*256 + G + B/256 - 32768
TERRARIUM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def estilo_mapa():
    sources, layers, attr = {}, [{"id": "fondo", "type": "background", "paint": {"background-color": "#eef0ec"}}], []
    if fondo in FUENTES:
        attr.append(FUENTES[fondo][1])
    if relieve:
        attr.append("Relieve © Esri")
    if fondo_arriba:
        if fondo in FUENTES or relieve:
            attr.append("Elevación: Terrarium (AWS Open Data)")
        estilo = {"version": 8, "sources": sources, "layers": layers}
        return "data:application/json;charset=utf-8," + urllib.parse.quote(json.dumps(estilo)), " · ".join(attr)
    if fondo in FUENTES:
        sources["base"] = {"type": "raster", "tiles": [FUENTES[fondo][0]], "tileSize": 256, "maxzoom": 19}
        layers.append({"id": "base", "type": "raster", "source": "base"})
    if relieve:
        sources["hill"] = {"type": "raster", "tileSize": 256, "tiles": [HILLSHADE]}
        layers.append({"id": "hill", "type": "raster", "source": "hill", "paint": {"raster-opacity": 0.45}})
    for n, c in enumerate(ss.capas):
        if c["tipo"] == "wms" and c["visible"]:
            sep = "&" if "?" in c["url"] else "?"
            url = (f"{c['url']}{sep}SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS={urllib.parse.quote(c['capa'])}"
                   f"&STYLES=&FORMAT=image/png&TRANSPARENT=true&SRS=EPSG:3857&WIDTH=256&HEIGHT=256&BBOX={{bbox-epsg-3857}}")
            sources[f"wms{n}"] = {"type": "raster", "tiles": [url], "tileSize": 256}
            layers.append({"id": f"wms{n}", "type": "raster", "source": f"wms{n}", "paint": {"raster-opacity": c["opacidad"]}})
    estilo = {"version": 8, "sources": sources, "layers": layers}
    return "data:application/json;charset=utf-8," + urllib.parse.quote(json.dumps(estilo)), " · ".join(attr)


def color_por_campo(serie):
    if serie is None:
        return None
    if pd.api.types.is_numeric_dtype(serie) and serie.nunique() > 12:
        v = (serie - serie.min()) / max(serie.max() - serie.min(), 1e-9)
        cmap = plt.get_cmap("viridis")
        return [[int(255 * x) for x in cmap(float(t))[:3]] for t in v.fillna(0)]
    pal = plt.get_cmap("tab20").colors
    cats = {v: i for i, v in enumerate(pd.unique(serie.astype(str)))}
    return [[int(255 * x) for x in pal[cats[str(v)] % 20]] for v in serie.astype(str)]


# ----------------------------------------------------------------------------- capas deck.gl
def url_worker():
    """Worker de loaders.gl servido por la propia app (static/), así no depende de unpkg.com."""
    try:
        base = st.context.url or ""
    except Exception:
        base = ""
    base = base.split("?")[0].split("#")[0]
    if not base.endswith("/"):
        base += "/"
    return urllib.parse.urljoin(base, "app/static/terrain-worker.js")


def capas_fondo_arriba():
    """Mapa base (y relieve) apoyado sobre el terreno, con la misma exageración vertical que el modelo."""
    L = []
    lift = 3.0   # m: evita que el mapa "pelee" visualmente con el techo del modelo
    dec = {"rScaler": 256 * ve, "gScaler": ve, "bScaler": ve / 256, "offset": (-32768 - Z_MIN + lift) * ve}
    texturas = []
    if fondo in FUENTES:
        texturas.append(("fondo_t", FUENTES[fondo][0], op_fondo))
    if relieve:
        texturas.append(("relieve_t", HILLSHADE, op_fondo * (0.45 if fondo in FUENTES else 1.0)))
    for n, (lid, tex, op) in enumerate(texturas):
        d = dict(dec, offset=dec["offset"] + n * 1.0 * ve)
        L.append(pdk.Layer("TerrainLayer", id=lid, elevation_data=TERRARIUM, texture=tex, elevation_decoder=d,
                           max_zoom=14, mesh_max_error=2.0 * ve, opacity=op, pickable=False, wireframe=False,
                           load_options={"terrain": {"workerUrl": url_worker()}}))
    # WMS: una imagen para toda el área, en un plano apenas por encima del techo del modelo
    lon, lat = grid["LON"], grid["LAT"]
    b = [float(lon.min()) - 0.02, float(lat.min()) - 0.02, float(lon.max()) + 0.02, float(lat.max()) + 0.02]
    z_top = float(Z(np.nanmax(np.where(grid["MASK"], np.nan, grid["SUP"][0])) + 5))
    for n, c in enumerate(ss.capas):
        if c["tipo"] == "wms" and c["visible"]:
            sep = "&" if "?" in c["url"] else "?"
            ancho = 2048
            alto = int(ancho * (b[3] - b[1]) / ((b[2] - b[0]) * np.cos(np.radians((b[1] + b[3]) / 2))))
            url = (f"{c['url']}{sep}SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS={urllib.parse.quote(c['capa'])}"
                   f"&STYLES=&FORMAT=image/png&TRANSPARENT=true&SRS=EPSG:4326&WIDTH={ancho}&HEIGHT={alto}"
                   f"&BBOX={b[0]},{b[1]},{b[2]},{b[3]}")
            L.append(pdk.Layer("BitmapLayer", id=f"wmsb{n}", image=url, opacity=c["opacidad"],
                               bounds=[[b[0], b[1], z_top], [b[0], b[3], z_top], [b[2], b[3], z_top], [b[2], b[1], z_top]]))
    return L


def _esquinas(G):
    """Valores en las esquinas de las celdas (promedio de las celdas vecinas válidas)."""
    ny, nx = G.shape
    P = np.full((ny + 2, nx + 2), np.nan)
    P[1:-1, 1:-1] = G
    st_ = np.stack([P[:-1, :-1], P[:-1, 1:], P[1:, :-1], P[1:, 1:]])
    with np.errstate(all="ignore"):
        import warnings
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(st_, axis=0)          # (ny+1, nx+1)


@st.cache_data(show_spinner=False)
def hoja_superficie(clave, ve_, cn_, ce_):
    G = HID["capas"][clave]
    C = _esquinas(G)
    LON, LAT = grid["LON"], grid["LAT"]
    nom, _, uni = SUPS_HIDRO[clave]
    vmin, vmax = np.nanpercentile(G, 2), np.nanpercentile(G, 98)
    cmap = plt.get_cmap("viridis" if clave == "NAPA_TOP" else "Blues")
    lat_c = LAT.max() - (LAT.max() - LAT.min()) * cn_ / 100
    lon_c = LON.max() - (LON.max() - LON.min()) * ce_ / 100
    filas = []
    for i, j in zip(*np.where(np.isfinite(G))):
        if LAT[i, j] > lat_c or LON[i, j] > lon_c:
            continue
        zz = [C[i, j], C[i, j + 1], C[i + 1, j + 1], C[i + 1, j]]
        zz = [G[i, j] if not np.isfinite(z) else z for z in zz]
        poly = [[round(float(LON[a, b]), 5), round(float(LAT[a, b]), 5), round(float((z - Z_MIN) * ve_), 1)]
                for (a, b), z in zip([(i, j), (i, j + 1), (i + 1, j + 1), (i + 1, j)], zz)]
        t = (G[i, j] - vmin) / max(vmax - vmin, 1e-6)
        col = [int(255 * x) for x in cmap(0.25 + 0.75 * float(np.clip(t, 0, 1)))[:3]]
        filas.append((poly, col, f"<b>{nom}</b><br>{G[i, j]:.1f} {uni}"))
    return pd.DataFrame(filas, columns=["poly", "color", "tip"])


def capas_hidro():
    L = []
    if HID is None:
        return L
    PT = HID["puntos"]
    for clave, (nom, capa_pts, uni) in SUPS_HIDRO.items():
        if not sel_hid.get(clave):
            continue
        d = hoja_superficie(clave, ve, cn, ce)
        if len(d):
            L.append(pdk.Layer("SolidPolygonLayer", id=f"hs_{clave}", data=d, get_polygon="poly", extruded=False,
                               get_fill_color="color", opacity=op_hid, pickable=True))
        if pts_hid:
            p = PT[PT.capa.str.startswith(capa_pts) & ~PT.outlier].copy()
            p = p[(p.lat <= lat_cut) & (p.lon <= lon_cut)]
            p["pos"] = [[round(x, 6), round(y, 6), round(float(z), 1)] for x, y, z in zip(p.lon, p.lat, Z(p.v))]
            p["tip"] = [f"<b>{c}</b><br>{nom}: {v:.1f} {uni}" for c, v in zip(p.Cod_UK, p.v)]
            L.append(pdk.Layer("ScatterplotLayer", id=f"hp_{clave}", data=p[["pos", "tip", "Cod_UK"]],
                               get_position="pos", get_radius=110, radius_min_pixels=3, get_fill_color=[20, 40, 90],
                               get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=1, pickable=True))
    return L


def capas_deck():
    L = []
    for n, c in enumerate(ss.capas):
        if c["tipo"] == "vector" and c["visible"]:
            gdf = c["gdf"].copy()
            if c.get("arriba"):
                import shapely
                z_top = float(Z(np.nanmax(np.where(grid["MASK"], np.nan, grid["SUP"][0]))) + 20)
                gdf["geometry"] = shapely.force_3d(gdf.geometry.values, z_top)
            cols = color_por_campo(gdf[c["campo"]]) if c["campo"] else None
            gj = json.loads(gdf.to_json(drop_id=True))
            props_cols = [x for x in c["campos"]][:8]
            for i, ft in enumerate(gj["features"]):
                pr = ft["properties"]
                pr["_fill"] = (cols[i] if cols else [230, 120, 60]) + [255]
                pr["tip"] = "<br>".join(f"<b>{k}</b>: {pr.get(k)}" for k in props_cols)
            L.append(pdk.Layer("GeoJsonLayer", id=f"usr{n}", data=gj, filled=True, stroked=True, opacity=c["opacidad"],
                               get_fill_color="properties._fill", get_line_color=[40, 40, 40], line_width_min_pixels=1,
                               point_radius_min_pixels=4, pickable=True))
    B = bloques_base()
    B = B[(B.k.isin([k for k in range(NF) if sel_form[k]]))]
    if not B.empty:
        lonc = np.array([p[0][0] for p in B.poly]); latc = np.array([p[0][1] for p in B.poly])
        B = B[(latc <= lat_cut) & (lonc <= lon_cut)].copy()
        zb = Z(B.bot.values)
        B["poly3"] = [[[x, y, round(float(z), 1)] for x, y in p] for p, z in zip(B.poly, zb)]
        B["elev"] = (B.e * ve).round(1)
        for k in range(NF):
            d = B[B.k == k]
            if len(d):
                L.append(pdk.Layer("SolidPolygonLayer", id=f"f{k}", data=d[["poly3", "elev", "tip"]],
                                   get_polygon="poly3", get_elevation="elev", extruded=True, wireframe=False,
                                   get_fill_color=COLF[k], opacity=opacidad, pickable=True, auto_highlight=True))
    if ver_pozos:
        T = TR[TR.Cod_UK.isin(pozos_ok.Cod_UK)].copy()
        if len(T):
            # cada tramo como una columna cuadrada (las líneas verticales puras no se dibujan bien en deck.gl)
            hy = grosor / 2 / 111320.0
            hx = hy / np.cos(np.radians(T.lat.values))
            zb = Z(T.Cota_hasta)
            T["poly"] = [[[round(x - dx, 6), round(y - hy, 6), round(float(z), 1)], [round(x + dx, 6), round(y - hy, 6), round(float(z), 1)],
                          [round(x + dx, 6), round(y + hy, 6), round(float(z), 1)], [round(x - dx, 6), round(y + hy, 6), round(float(z), 1)]]
                         for x, y, dx, z in zip(T.lon, T.lat, hx, zb)]
            T["elev"] = ((T.Cota_desde - T.Cota_hasta).clip(lower=0.1) * ve).round(1)
            T["color"] = [COLF[f] if 0 <= f <= NF else GRIS for f in T.f]
            T["tip"] = [f"<b>{c}</b><br>{NOMF[f] if 0 <= f <= NF else 'Sin dato / otra'}<br>{d:g}–{h:g} m "
                        f"({z1:.1f} a {z2:.1f} m s.n.m.)<br>Fuente: {fu}<br><i>{l[:80]}</i>"
                        for c, f, d, h, z1, z2, fu, l in zip(T.Cod_UK, T.f, T.Desde, T.Hasta, T.Cota_desde,
                                                             T.Cota_hasta, T.Fuente, T.Litologia)]
            L.append(pdk.Layer("SolidPolygonLayer", id="tramos", data=T[["poly", "elev", "color", "tip", "Cod_UK"]],
                               get_polygon="poly", get_elevation="elev", extruded=True, get_fill_color="color",
                               pickable=True, auto_highlight=True))
    if ver_nombres and len(pozos_ok):
        P = pozos_ok.copy()
        P["pos"] = [[x, y, float(z) + 8 * ve] for x, y, z in zip(P.lon, P.lat, Z(P.cota_boca))]
        P["tip"] = [f"<b>{c}</b><br>Prof. total {p:g} m · boca {z:.1f} m s.n.m.<br>{f}"
                    for c, p, z, f in zip(P.Cod_UK, P.prof_total, P.cota_boca, P.fuente)]
        L.append(pdk.Layer("TextLayer", id="nombres", data=P[["pos", "Cod_UK", "tip"]], get_position="pos",
                           get_text="Cod_UK", get_size=12, get_color=[20, 20, 20], background=True,
                           get_background_color=[255, 255, 255, 220], get_text_anchor="'middle'",
                           get_alignment_baseline="'bottom'", billboard=True, pickable=True))
    L += capas_hidro()
    if fondo_arriba:
        L += capas_fondo_arriba()      # al final: se dibuja sobre (y tapa) la litología interpolada
    return L


# ----------------------------------------------------------------------------- interfaz principal
st.markdown("### Modelo conceptual del Sistema Acuífero Raigón – San José")
tab_mapa, tab_corte, tab_pozo, tab_hid, tab_info = st.tabs(["🗺️ Mapa 3D", "📐 Corte", "🕳️ Pozo", "💧 Hidrogeología",
                                                             "ℹ️ Acerca del modelo"])

with tab_mapa:
    estilo, atribucion = estilo_mapa()
    vista = pdk.ViewState(longitude=meta["centro"][0], latitude=meta["centro"][1], zoom=9.6, pitch=50, bearing=-15,
                          max_pitch=85)
    deck = DeckCompacto(layers=capas_deck(), initial_view_state=vista, map_style=estilo, map_provider="carto",
                    tooltip={"html": "{tip}", "style": {"backgroundColor": "white", "color": "#1d2327",
                                                        "fontSize": "12px", "border": "1px solid #ccc"}})
    ev = st.pydeck_chart(deck, height=720, on_select="rerun",
                         selection_mode="single-object", key="mapa")
    try:
        objs = ev.selection.objects if ev else {}
        for lid in ("tramos", "nombres"):
            if objs.get(lid):
                ss.pozo_sel = objs[lid][0].get("Cod_UK")
    except Exception:
        pass
    cols = st.columns(len(FORMS) + 1)
    for c, f in zip(cols, FORMS):
        c.markdown(f"<span style='display:inline-block;width:12px;height:12px;background:{f['color']};"
                   f"border-radius:2px;margin-right:4px'></span>{f['nombre']}", unsafe_allow_html=True)
    st.caption(f"Arrastrar: mover · Ctrl + arrastrar (o clic derecho): rotar e inclinar · Rueda: zoom · "
               f"Clic en un pozo: ver su ficha en la pestaña «Pozo». {atribucion}")
    if ss.pozo_sel:
        st.info(f"Pozo seleccionado: **{ss.pozo_sel}** (detalle en la pestaña «Pozo»)")

# ---- corte
with tab_corte:
    gx, gy, SUP, MASK = grid["gx"], grid["gy"], grid["SUP"], grid["MASK"]
    celda = meta["celda"]

    def valor_en(G, x, y):
        i = np.clip(np.round((np.asarray(y) - gy[0]) / celda).astype(int), 0, len(gy) - 1)
        j = np.clip(np.round((np.asarray(x) - gx[0]) / celda).astype(int), 0, len(gx) - 1)
        return G[i, j]

    c1, c2 = st.columns([1, 2])
    with c1:
        modo = st.radio("Definir el corte", ["Predefinido", "Entre dos pozos", "Coordenadas UTM"])
        if modo == "Predefinido":
            nom = st.selectbox("Corte", list(meta["cortes"]))
            p0, p1 = meta["cortes"][nom]
        elif modo == "Entre dos pozos":
            lista = sorted(PO.Cod_UK)
            a = st.selectbox("Desde el pozo", lista, index=0)
            b = st.selectbox("Hasta el pozo", lista, index=min(len(lista) - 1, 50))
            ra, rb = PO.set_index("Cod_UK").loc[a], PO.set_index("Cod_UK").loc[b]
            p0, p1 = [ra.X, ra.Y], [rb.X, rb.Y]
            nom = f"{a} – {b}"
        else:
            x0 = st.number_input("X inicio", value=505000.0, step=500.0)
            y0 = st.number_input("Y inicio", value=6172000.0, step=500.0)
            x1 = st.number_input("X fin", value=578000.0, step=500.0)
            y1 = st.number_input("Y fin", value=6172000.0, step=500.0)
            p0, p1, nom = [x0, y0], [x1, y1], "Corte personalizado"
        buf = st.slider("Proyectar pozos a menos de (m)", 100, 5000, 1500, 100)
        ve_c = st.slider("Exageración vertical del corte", 10, 300, 100, 10)
        nombres_c = st.checkbox("Nombres de pozo en el corte", value=True)
        ver_hid_corte = {}
        if HID is not None:
            ver_hid_corte["PIEZO"] = st.checkbox("Nivel piezométrico regional", value=True)
            ver_hid_corte["PIEZO_MON"] = st.checkbox("Nivel piezométrico (monitoreo jun-2024)", value=False)
            ver_hid_corte["NAPA_TOP"] = st.checkbox("Techo de la 1ª napa con aporte", value=False)
    p0, p1 = np.array(p0, float), np.array(p1, float)
    Lng = np.linalg.norm(p1 - p0)
    with c2:
        n = 500
        xs, ys = np.linspace(p0[0], p1[0], n), np.linspace(p0[1], p1[1], n)
        d = np.hypot(xs - p0[0], ys - p0[1]) / 1000
        m = valor_en(MASK, xs, ys).astype(bool)
        prof = np.array([valor_en(SUP[k], xs, ys) for k in range(NF + 1)])
        prof[:, m] = np.nan
        ok_d = np.isfinite(prof[0])
        if Lng < 500:
            st.warning("El corte es muy corto: elegí puntos más separados.")
        elif not ok_d.any():
            st.warning("La línea no pasa por el área modelada.")
        else:
            d0, d1 = d[ok_d].min(), d[ok_d].max()
            z0, z1 = np.nanmin(prof[-1]) - 5, np.nanmax(prof[0]) + 12
            alto = float(np.clip(11 * ((z1 - z0) / 1000 * ve_c) / max(d1 - d0, 0.1), 2.5, 9))
            ve_real = alto / 11 * max(d1 - d0, 0.1) / ((z1 - z0) / 1000)
            fig, ax = plt.subplots(figsize=(11, alto + 1.6))
            for k in range(NF):
                ax.fill_between(d, prof[k + 1], prof[k], color=FORMS[k]["color"], lw=0,
                                label=FORMS[k]["nombre"] + (" (base dibujada)" if k == NF - 1 else ""))
            ax.plot(d, prof[0], "k", lw=0.8)
            if HID is not None:
                hz = HID["capas"]
                for clave, estilo_l, lab in [("PIEZO", dict(color="#1f4e9c", lw=1.6, ls="--"), "Nivel piezométrico regional"),
                                             ("PIEZO_MON", dict(color="#1f9ce0", lw=1.2, ls=":"), "Nivel piezométrico jun-2024"),
                                             ("NAPA_TOP", dict(color="#6a1b9a", lw=1.0, ls="-."), "Techo 1ª napa con aporte")]:
                    if ver_hid_corte.get(clave):
                        zv = valor_en(hz[clave], xs, ys).astype(float)
                        zv[m] = np.nan
                        ax.plot(d, zv, label=lab, zorder=7, **estilo_l)
            u = (p1 - p0) / Lng
            rel = PO[["X", "Y"]].values - p0
            a_ = pd.Series(rel @ u, index=PO.index)
            dist = pd.Series(np.abs(rel @ np.array([-u[1], u[0]])), index=PO.index)
            cerca = PO[(dist < buf) & (a_ >= 0) & (a_ <= Lng) & PO.Cod_UK.isin(pozos_ok.Cod_UK)].copy()
            cerca["d"] = a_[cerca.index] / 1000
            ancho = max(d[-1] / 250, 0.03)
            for _, pw in cerca.iterrows():
                for _, t in TR[TR.Cod_UK == pw.Cod_UK].iterrows():
                    col = FORMS[t.f]["color"] if 0 <= t.f < NF else (meta["basamento"]["color"] if t.f == NF else meta["gris"])
                    ax.add_patch(Rectangle((pw.d - ancho / 2, t.Cota_hasta), ancho, t.Cota_desde - t.Cota_hasta,
                                           facecolor=col, edgecolor="k", lw=0.4, zorder=5))
                if nombres_c:
                    ax.text(pw.d, pw.cota_boca + 1, pw.Cod_UK, rotation=90, fontsize=6.5, ha="center", va="bottom", zorder=6)
            ax.set_xlabel("Distancia (km)"); ax.set_ylabel("Cota (m s.n.m.)")
            ax.set_title(f"{nom} · exageración vertical ≈{ve_real:.0f}x · {len(cerca)} pozos proyectados (±{buf} m)")
            ax.set_ylim(z0, z1)
            ax.set_xlim(max(d0 - 0.5, 0), d1 + 0.5)
            ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=4, fontsize=8, frameon=False)
            ax.grid(alpha=0.25)
            fig.tight_layout()
            st.pyplot(fig)
            buf_png = io.BytesIO(); fig.savefig(buf_png, dpi=200, bbox_inches="tight")
            st.download_button("Descargar corte (PNG)", buf_png.getvalue(), file_name="corte_SAR.png", mime="image/png")
            plt.close(fig)
            # ubicación
            fig2, ax2 = plt.subplots(figsize=(6, 3.6))
            ax2.pcolormesh(gx, gy, np.where(MASK, np.nan, SUP[4]), cmap="Blues_r", shading="auto")
            ax2.scatter(PO.X, PO.Y, s=2, c="#555")
            ax2.plot([p0[0], p1[0]], [p0[1], p1[1]], "r-", lw=2)
            ax2.set_aspect("equal"); ax2.set_title("Ubicación (fondo: techo del Raigón)", fontsize=9)
            ax2.ticklabel_format(style="plain"); ax2.tick_params(labelsize=7)
            st.pyplot(fig2, width="content")
            plt.close(fig2)

# ---- pozo
with tab_pozo:
    lista = sorted(PO.Cod_UK)
    idx = lista.index(ss.pozo_sel) if ss.pozo_sel in lista else 0
    cod = st.selectbox("Pozo", lista, index=idx, help="También podés hacer clic en un pozo del mapa 3D.")
    pw = PO.set_index("Cod_UK").loc[cod]
    t = TR[TR.Cod_UK == cod].sort_values("Desde")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cota de boca", f"{pw.cota_boca:.1f} m s.n.m.")
    c2.metric("Profundidad", f"{pw.prof_total:g} m")
    c3.metric("Tramos", len(t))
    c4.metric("Fuente", pw.fuente)
    st.caption(f"UTM 21S: X {pw.X:.0f} · Y {pw.Y:.0f}")
    cl, ct = st.columns([1, 3])
    with cl:
        fig, ax = plt.subplots(figsize=(2.4, 6))
        for _, r in t.iterrows():
            col = FORMS[r.f]["color"] if 0 <= r.f < NF else (meta["basamento"]["color"] if r.f == NF else meta["gris"])
            ax.add_patch(Rectangle((0, r.Cota_hasta), 1, r.Cota_desde - r.Cota_hasta, facecolor=col, edgecolor="k", lw=0.5))
        ax.set_xlim(0, 1); ax.set_ylim(t.Cota_hasta.min() - 1, t.Cota_desde.max() + 1)
        ax.set_xticks([]); ax.set_ylabel("Cota (m s.n.m.)"); ax.set_title(cod, fontsize=9)
        fig.tight_layout(); st.pyplot(fig); plt.close(fig)
    with ct:
        tabla = t[["Desde", "Hasta", "Cota_desde", "Cota_hasta", "Formacion", "Litologia_simpl", "Litologia"]].rename(
            columns={"Cota_desde": "Cota desde", "Cota_hasta": "Cota hasta", "Formacion": "Formación",
                     "Litologia_simpl": "Litología (clase)", "Litologia": "Descripción"})
        st.dataframe(tabla, hide_index=True)
        st.download_button("Descargar este pozo (CSV)", tabla.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"{cod}_litologia.csv", mime="text/csv")
        filt = TR[TR.Cod_UK.isin(pozos_ok.Cod_UK)].drop(columns=["f"])
        st.download_button(f"Descargar pozos filtrados ({pozos_ok.Cod_UK.nunique()} pozos, CSV)",
                           filt.to_csv(index=False).encode("utf-8-sig"), file_name="pozos_filtrados_SAR.csv",
                           mime="text/csv")

# ---- hidrogeología
with tab_hid:
    if HID is None:
        st.info("No hay capas hidrogeológicas: corré `python preparar_datos.py` para generarlas.")
    else:
        hm = meta.get("hidro", {})
        hc, PT, MON, TEND = HID["capas"], HID["puntos"], HID["mon"], HID["tend"]
        gx, gy = grid["gx"], grid["gy"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Pozos con nivel estático", hm.get("n_ne", "–"))
        k2.metric(f"Red de monitoreo ({hm.get('fecha_mon', '')})", hm.get("n_mon", "–"))
        k3.metric("Tendencia mediana del nivel", f"{hm.get('tendencia_mediana', float('nan')) * 100:+.1f} cm/año",
                  help=f"Pendiente de la recta de la carga hidráulica en {hm.get('n_tendencia', '–')} pozos con ≥10 lecturas")
        conf = hc["CONF"]
        k4.metric("Área semiconfinada", f"{np.nanmean(conf) * 100:.0f} %",
                  help="Celdas donde la carga hidráulica supera el techo del acuífero (Libertad actúa como acuitardo)")
        c1, c2 = st.columns([1, 3])
        with c1:
            clave = st.radio("Capa", list(MAPAS_HIDRO), format_func=lambda k: MAPAS_HIDRO[k][0])
            ver_pts = st.checkbox("Puntos de dato", value=True)
            ver_flujo = st.checkbox("Dirección de flujo (regional)", value=clave in ("PIEZO", "PIEZO_MON"))
            ver_out = st.checkbox("Marcar valores descartados (outliers)", value=False)
        with c2:
            tit, cmap, uni, vmin, vmax = MAPAS_HIDRO[clave]
            G = hc[clave]
            fig, ax = plt.subplots(figsize=(10, 6.2))
            im = ax.pcolormesh(gx, gy, G, cmap=cmap, shading="auto", vmin=vmin, vmax=vmax)
            if clave in ("PIEZO", "PIEZO_MON", "NAPA_TOP", "PROF_AGUA", "SAT"):
                paso = 5 if clave != "PROF_AGUA" else 2.5
                lv = np.arange(np.floor(np.nanmin(G) / paso) * paso, np.nanmax(G) + paso, paso)
                cs = ax.contour(gx, gy, G, levels=lv, colors="#2b1a4f", linewidths=0.7)
                ax.clabel(cs, fmt="%g", fontsize=7)
            if clave == "SAT":
                ax.contour(gx, gy, np.nan_to_num(conf, nan=0), levels=[0.5], colors="red", linewidths=1.2)
            if ver_flujo:
                s_ = 4
                GX, GY = np.meshgrid(gx, gy)
                ax.quiver(GX[::s_, ::s_], GY[::s_, ::s_], -hc["dzx"][::s_, ::s_], -hc["dzy"][::s_, ::s_],
                          color="#7a3ca8", scale=0.12, width=0.0022)
            capa_pts = {"PIEZO": "NE regional", "PIEZO_MON": "Monitoreo", "NAPA_TOP": "Techo 1ª napa",
                        "LOGT": "Transmisividad", "PROF_AGUA": "NE regional", "SAT": "NE regional", "CONF": "NE regional"}[clave]
            p = PT[PT.capa.str.startswith(capa_pts)]
            if ver_pts:
                ax.scatter(p[~p.outlier].X, p[~p.outlier].Y, s=5, c="k", lw=0)
            if ver_out:
                ax.scatter(p[p.outlier].X, p[p.outlier].Y, s=18, c="r", marker="x", lw=0.8)
            plt.colorbar(im, ax=ax, shrink=0.75, label=uni)
            ax.set_aspect("equal"); ax.ticklabel_format(style="plain"); ax.tick_params(labelsize=7)
            ax.set_title(tit + ("  ·  rojo: límite semiconfinado" if clave == "SAT" else ""), fontsize=11)
            fig.tight_layout(); st.pyplot(fig)
            bpng = io.BytesIO(); fig.savefig(bpng, dpi=200, bbox_inches="tight"); plt.close(fig)
            d1, d2 = st.columns(2)
            d1.download_button("Descargar mapa (PNG)", bpng.getvalue(), file_name=f"{clave}_SAR.png", mime="image/png")
            GX, GY = np.meshgrid(gx, gy)
            grid_csv = pd.DataFrame({"X": GX.ravel(), "Y": GY.ravel(), clave: G.ravel()}).dropna()
            d2.download_button("Descargar grilla (CSV X, Y, valor)", grid_csv.to_csv(index=False).encode("utf-8"),
                               file_name=f"{clave}_grilla_500m.csv", mime="text/csv")
        st.markdown("#### Hidrogramas de la red de monitoreo")
        orden = TEND.sort_values("n", ascending=False).Cod_UK.tolist()
        resto = sorted(set(MON.Cod_UK) - set(orden))
        sel_p = st.multiselect("Pozos", orden + resto, default=orden[:5])
        if sel_p:
            fig, ax = plt.subplots(figsize=(11, 4))
            for c_ in sel_p:
                s_ = MON[MON.Cod_UK == c_].sort_values("fecha")
                ax.plot(s_.fecha, s_.carga, ".-", ms=3, lw=0.9, label=c_)
            ax.set_ylabel("Carga hidráulica (m s.n.m.)"); ax.grid(alpha=0.3); ax.legend(ncol=5, fontsize=8)
            fig.tight_layout(); st.pyplot(fig); plt.close(fig)
        st.dataframe(TEND.sort_values("tendencia_m_anio").rename(columns={
            "tendencia_m_anio": "Tendencia (m/año)", "amplitud_m": "Oscilación P5–P95 (m)", "n": "Lecturas"}).round(3),
            hide_index=True)
        st.download_button("Descargar serie completa de monitoreo (CSV)", MON.to_csv(index=False).encode("utf-8-sig"),
                           file_name="monitoreo_niveles_SAR.csv", mime="text/csv")
        st.caption("Superficie regional: NE de perforación y relevamientos de distintas fechas (la mayoría sin fecha); "
                   "el descenso medio de ~5 cm/año es chico frente a los 40 m de relieve piezométrico, pero localmente "
                   "puede haber diferencias de varios metros. La superficie de monitoreo es de una sola fecha pero con "
                   "pocos pozos. Transmisividad: T ≈ 1,22·Q/s (Logan, 1964), solo como orden de magnitud.")

# ---- info
with tab_info:
    st.markdown(f"""
**Datos:** base de pozos DINAGUA acotada (`{meta['base']}`), generada el {meta['generado']}.
Cotas de boca: Z_m cuando existe, si no MDT IDE Uruguay 2,5 m (vuelo 2017-2018). UTM 21S.

**Método:** superficies 2.5D (RBF lineal con suavizado, celda {meta['celda']} m) a partir de los contactos
techo y base del acuífero en los pozos, con validación cruzada para descartar valores anómalos y usando los pozos
que no alcanzan una superficie como restricción. Dentro de la cobertura (Suelo, Dolores, Libertad) y del acuífero
(Chuy, Raigón) el espesor de cada formación se interpola y se reescala al espesor del paquete.

**Limitaciones**
- **Camacho:** la mayoría de los pozos terminan dentro; su espesor es un *mínimo* donde no se atraviesa.
- **Fray Bentos:** ningún pozo llega a su base; se dibuja una lámina de {meta['esp_fb']:g} m.
- **Pozos excluidos** ({len(meta['excluidos'])}) por cota de boca dudosa (> 15 m de la topografía): {', '.join(meta['excluidos'])}.
- Celdas a más de 3 km de un pozo con contacto quedan fuera del modelo.

**Capas hidrogeológicas** (pestaña «Hidrogeología» y barra lateral)
- *Superficie piezométrica regional:* carga = cota de boca − nivel estático (hoja Hidráulica), promediando pozos a
  menos de 20 m y descartando NE mayores que la profundidad del pozo, NE > 60 m, bocas dudosas y valores anómalos
  por validación cruzada. Mezcla fechas (la mayoría sin fecha).
- *Superficie de monitoreo:* misma fecha para todos los pozos (hoja Historico niveles, junio 2024). Las fechas de esa
  hoja venían mal convertidas por Excel y se reconstruyeron respetando el orden de las columnas.
- *Techo de la 1ª napa con aporte:* cota del primer horizonte con agua declarado (hoja Ubic. Napas), solo en Chuy,
  Raigón o pozos sin litología. Se excluyen los datos marcados «no se sabe si es inicio o fin».
- *Derivadas:* profundidad al agua, espesor saturado (Chuy + Raigón), zona semiconfinada (carga por encima del techo
  del acuífero), dirección de flujo (gradiente de la superficie regional) y transmisividad por capacidad específica
  (T ≈ 1,22·Q/s, Logan 1964; orden de magnitud).

Para actualizar el modelo con una base nueva: `python preparar_datos.py ruta/base.xlsx` y recargar la app.
""")
