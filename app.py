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


# ----------------------------------------------------------------------------- datos
@st.cache_data(show_spinner="Cargando modelo…")
def cargar():
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    g = np.load(DATA / "grilla.npz")
    grid = {k: g[k] for k in g.files}
    tr = pd.read_parquet(DATA / "tramos.parquet")
    po = pd.read_parquet(DATA / "pozos.parquet")
    return meta, grid, tr, po


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
        poly = [[round(float(LON[i, j]), 6), round(float(LAT[i, j]), 6)],
                [round(float(LON[i, j + 1]), 6), round(float(LAT[i, j + 1]), 6)],
                [round(float(LON[i + 1, j + 1]), 6), round(float(LAT[i + 1, j + 1]), 6)],
                [round(float(LON[i + 1, j]), 6), round(float(LAT[i + 1, j]), 6)]]
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
def estilo_mapa():
    fuentes = {
        "Imagen satelital (Esri)": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                                    "Imagen © Esri, Maxar, Earthstar Geographics"),
        "Mapa de calles (OSM)": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "© colaboradores de OpenStreetMap"),
        "Topográfico (Esri)": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", "© Esri"),
    }
    sources, layers, attr = {}, [{"id": "fondo", "type": "background", "paint": {"background-color": "#eef0ec"}}], []
    if fondo in fuentes:
        url, a = fuentes[fondo]
        sources["base"] = {"type": "raster", "tiles": [url], "tileSize": 256, "maxzoom": 19}
        layers.append({"id": "base", "type": "raster", "source": "base"})
        attr.append(a)
    if relieve:
        sources["hill"] = {"type": "raster", "tileSize": 256, "tiles": [
            "https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}"]}
        layers.append({"id": "hill", "type": "raster", "source": "hill", "paint": {"raster-opacity": 0.45}})
        attr.append("Relieve © Esri")
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
        B["poly3"] = [[[x, y, float(z)] for x, y in p] for p, z in zip(B.poly, zb)]
        B["elev"] = B.e * ve
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
            T["poly"] = [[[x - dx, y - hy, float(z)], [x + dx, y - hy, float(z)], [x + dx, y + hy, float(z)],
                          [x - dx, y + hy, float(z)]] for x, y, dx, z in zip(T.lon, T.lat, hx, zb)]
            T["elev"] = (T.Cota_desde - T.Cota_hasta).clip(lower=0.1) * ve
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
        P["pos"] = [[x, y, float(z) + 40] for x, y, z in zip(P.lon, P.lat, Z(P.cota_boca))]
        P["tip"] = [f"<b>{c}</b><br>Prof. total {p:g} m · boca {z:.1f} m s.n.m.<br>{f}"
                    for c, p, z, f in zip(P.Cod_UK, P.prof_total, P.cota_boca, P.fuente)]
        L.append(pdk.Layer("TextLayer", id="nombres", data=P[["pos", "Cod_UK", "tip"]], get_position="pos",
                           get_text="Cod_UK", get_size=12, get_color=[20, 20, 20], background=True,
                           get_background_color=[255, 255, 255, 220], get_text_anchor="'middle'",
                           get_alignment_baseline="'bottom'", billboard=True, pickable=True))
    return L


# ----------------------------------------------------------------------------- interfaz principal
st.markdown("### Modelo conceptual del Sistema Acuífero Raigón – San José")
tab_mapa, tab_corte, tab_pozo, tab_info = st.tabs(["🗺️ Mapa 3D", "📐 Corte", "🕳️ Pozo", "ℹ️ Acerca del modelo"])

with tab_mapa:
    estilo, atribucion = estilo_mapa()
    vista = pdk.ViewState(longitude=meta["centro"][0], latitude=meta["centro"][1], zoom=9.6, pitch=50, bearing=-15,
                          max_pitch=85)
    deck = pdk.Deck(layers=capas_deck(), initial_view_state=vista, map_style=estilo, map_provider="carto",
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

Para actualizar el modelo con una base nueva: `python preparar_datos.py ruta/base.xlsx` y recargar la app.
""")
