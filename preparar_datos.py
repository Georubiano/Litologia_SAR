"""Regenera la carpeta data/ de la app a partir de la base de pozos acotada.

Uso:
    python preparar_datos.py                      # usa entrada/base_acotada.xlsx
    python preparar_datos.py ruta/a/otra_base.xlsx

La base tiene que tener las hojas "Pozos" y "Litología" con las columnas de la base acotada
(Z_boca (m), Inicio/Fin Acotado, Formacion_unificada, Version_tramos, etc.).
"""
import json, sys, datetime
from pathlib import Path
import numpy as np
import pandas as pd
from pyproj import Transformer

from modelo_sar import contactos as ct
from modelo_sar import modelo as md

AQUI = Path(__file__).parent
BASE = Path(sys.argv[1]) if len(sys.argv) > 1 else AQUI / "entrada" / "base_acotada.xlsx"
DATA = AQUI / "data"
DATA.mkdir(exist_ok=True)

print("Leyendo", BASE)
pz, lit = ct.leer_base(BASE)
tramos = ct.tabla_tramos(lit)
c, k, prob = ct.extraer_contactos(lit)
topo = ct.topografia(pz)
print(f"{tramos.Cod_UK.nunique()} pozos con litología, {len(c)} contactos, {len(k)} pozos que no alcanzan una "
      f"superficie, {len(prob)} con orden invertido")

M = md.construir(c, k, topo, tramos)
tf = Transformer.from_crs(32721, 4326, always_xy=True)

# esquinas de celdas en lon/lat
gx, gy = M["gx"], M["gy"]
cx = np.concatenate([gx - md.CELDA / 2, [gx[-1] + md.CELDA / 2]])
cy = np.concatenate([gy - md.CELDA / 2, [gy[-1] + md.CELDA / 2]])
LON, LAT = tf.transform(*np.meshgrid(cx, cy))
np.savez_compressed(DATA / "grilla.npz", gx=gx, gy=gy, MASK=M["MASK"], SUP=M["SUP"], LON=LON, LAT=LAT)

tr = M["tramos"].copy()
tr["lon"], tr["lat"] = tf.transform(tr.X.values, tr.Y.values)
tr["f"] = tr.Formacion.map({f: i for i, f in enumerate(md.ORDEN)}).fillna(-1).astype(int)
tr.loc[tr.Formacion == "Basamento", "f"] = len(md.ORDEN)
tr["Litologia"] = tr.Litologia.fillna("").astype(str)
tr["Formacion"] = tr.Formacion.fillna("Sin dato")
tr["Fuente"] = tr.Fuente.fillna("Sin dato").astype(str)
tr.to_parquet(DATA / "tramos.parquet", index=False)

po = tr.groupby("Cod_UK").agg(X=("X", "first"), Y=("Y", "first"), lon=("lon", "first"), lat=("lat", "first"),
                              cota_boca=("Cota_desde", "max"), prof_total=("Hasta", "max"),
                              fuente=("Fuente", "first"),
                              formaciones=("Formacion", lambda s: " | ".join(dict.fromkeys(s)))).reset_index()
po.to_parquet(DATA / "pozos.parquet", index=False)
c.to_parquet(DATA / "contactos.parquet", index=False)
prob.to_csv(DATA / "pozos_orden_invertido.csv", index=False)
M["outliers"].to_csv(DATA / "outliers_revisar.csv", index=False)

valid = np.where(M["MASK"], np.nan, M["SUP"][-1])
meta = dict(
    generado=datetime.datetime.now().isoformat(timespec="minutes"), base=str(BASE.name),
    formaciones=[{"id": f, "nombre": md.FORM[f][0], "color": md.FORM[f][1]} for f in md.ORDEN],
    basamento={"nombre": "Basamento", "color": md.FORM["Basamento"][1]}, gris=md.GRIS,
    excluidos=M["excluidos"], z_min=float(np.nanmin(valid)), celda=md.CELDA, esp_fb=md.ESP_FB,
    centro=[float(np.mean(LON)), float(np.mean(LAT))],
    cortes={"A-A' (O-E)": [[505000, 6172000], [578000, 6172000]], "B-B' (S-N)": [[540000, 6152000], [540000, 6193000]]},
)
# ---- capas hidrogeológicas (piezometría, napas, transmisividad)
from modelo_sar import hidro
H, HP, _ = hidro.construir(BASE, {"gx": gx, "gy": gy, "MASK": M["MASK"], "SUP": M["SUP"]}, M["tramos"])
np.savez_compressed(DATA / "hidro.npz", **H)
pts = []
for k in ("ne", "mon", "napa", "T"):
    d = HP[k].copy()
    d["lon"], d["lat"] = tf.transform(d.X.values, d.Y.values)
    pts.append(d[["Cod_UK", "X", "Y", "lon", "lat", "v", "n", "residuo_LOO", "outlier", "capa"]])
pd.concat(pts).to_parquet(DATA / "hidro_puntos.parquet", index=False)
mon = HP["monitoreo"][["Cod_UK", "Propietario", "X", "Y", "Z_boca", "fecha", "prof_agua", "carga"]]
mon.to_parquet(DATA / "monitoreo.parquet", index=False)
HP["tendencia"].to_csv(DATA / "tendencia_niveles.csv", index=False)
HP["napas_todas"].to_csv(DATA / "napas_con_formacion.csv", index=False)
HP["napa_vs_S1"].to_csv(DATA / "napa_vs_techo_acuifero.csv", index=False)
HP["descartes_ne"].to_csv(DATA / "NE_descartados.csv", index=False)
meta["hidro"] = {"fecha_mon": "2024-06", "n_ne": int(len(HP["ne"])), "n_mon": int(len(HP["mon"])),
                 "n_napa": int(len(HP["napa"])), "n_T": int(len(HP["T"])),
                 "tendencia_mediana": float(HP["tendencia"].tendencia_m_anio.median()),
                 "n_tendencia": int(len(HP["tendencia"]))}

(DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print("Listo:", sorted(p.name for p in DATA.iterdir()))
