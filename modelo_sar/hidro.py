"""Capas hidrogeológicas del SAR sobre la grilla del modelo geológico.

Superficies (misma grilla de 500 m que el modelo geológico):
  PIEZO      superficie piezométrica regional (NE de perforación/relevamientos, fechas mezcladas)
  PIEZO_MON  superficie piezométrica de la red de monitoreo en una fecha (por defecto junio 2024)
  NAPA_TOP   cota del primer horizonte con aporte (hoja Ubic. Napas)
  Derivadas: profundidad al agua, espesor saturado del acuífero, zona semiconfinada, gradiente y dirección de flujo,
  transmisividad estimada (Logan) a partir de la capacidad específica.
"""

from pathlib import Path
import numpy as np
import pandas as pd
from scipy.interpolate import RBFInterpolator
from scipy.spatial import cKDTree



try:
    from . import datos_hidro as dh
except ImportError:  # ejecutado fuera del paquete
    import datos_hidro as dh

DIST_MAX = 3000


def _loo(xy, z, k=12):
    d, idx = cKDTree(xy).query(xy, k=min(k + 1, len(z)))
    w = 1 / np.maximum(d[:, 1:], 1) ** 2
    return z - (w * z[idx[:, 1:]]).sum(1) / w.sum(1)


def qc_puntos(df, col, piso, tol=20):
    """Promedia puntos a menos de tol m y marca outliers por validación cruzada (IDW, 12 vecinos)."""
    d = df.copy()
    d["_k"] = (d.X // tol).astype(int).astype(str) + "_" + (d.Y // tol).astype(int).astype(str)
    g = d.groupby("_k").agg(Cod_UK=("Cod_UK", "first"), n=("Cod_UK", "size"), X=("X", "mean"), Y=("Y", "mean"),
                            v=(col, "mean")).reset_index(drop=True)
    r = _loo(g[["X", "Y"]].values, g.v.values)
    mad = np.median(np.abs(r - np.median(r))) * 1.4826
    g["residuo_LOO"] = r
    g["outlier"] = np.abs(r) > max(3.5 * mad, piso)
    return g


class Grid:
    def __init__(self, gx, gy, mask):
        self.gx, self.gy, self.MASK = gx, gy, mask
        self.GX, self.GY = np.meshgrid(gx, gy)
        self.XY = np.column_stack([self.GX.ravel(), self.GY.ravel()])
        self.celda = gx[1] - gx[0]

    def en(self, G, x, y):
        i = np.clip(np.round((np.asarray(y) - self.gy[0]) / self.celda).astype(int), 0, len(self.gy) - 1)
        j = np.clip(np.round((np.asarray(x) - self.gx[0]) / self.celda).astype(int), 0, len(self.gx) - 1)
        return G[i, j]

    def rbf(self, x, y, v, suav=20.0, vecinos=40):
        f = RBFInterpolator(np.column_stack([x, y]), v, kernel="linear", smoothing=suav,
                            neighbors=min(vecinos, len(v)))
        return f(self.XY).reshape(self.GX.shape)

    def lejos(self, x, y, dmax=DIST_MAX):
        d, _ = cKDTree(np.column_stack([x, y])).query(self.XY)
        return (d > dmax).reshape(self.GX.shape)


def construir(base, grilla, tramos, fecha_mon="2024-06-01", log=print):
    x, pz = dh.leer(base)
    gx, gy, MASK, SUP = grilla["gx"], grilla["gy"], grilla["MASK"], grilla["SUP"]
    g = Grid(gx, gy, MASK)
    TOPO, S1, S2 = SUP[0], SUP[3], SUP[5]          # techo Chuy = techo acuífero; techo Camacho = base acuífero
    xmin, xmax, ymin, ymax = gx[0], gx[-1], gy[0], gy[-1]

    def en_area(d):
        return d[(d.X.between(xmin, xmax)) & (d.Y.between(ymin, ymax))]

    # ---------------- 1. nivel estático regional
    h = dh.nivel_estatico(x, pz)
    h = en_area(h[h.NE.notna() & h.Z_boca.notna()])
    h["descarte"] = ""
    h.loc[h.NE > h.Prof_total, "descarte"] = "NE mayor que la profundidad del pozo"
    h.loc[h.NE > 60, "descarte"] = "NE > 60 m (probable nivel dinámico o error)"
    h["Z_topo"] = g.en(TOPO, h.X, h.Y)
    h.loc[(h.Z_boca - h.Z_topo).abs() > 15, "descarte"] = "cota de boca > 15 m de la topografía"
    hv = h[h.descarte == ""]
    P = qc_puntos(hv, "carga", piso=4.0)
    ok = P[~P.outlier]
    PIEZO = np.clip(g.rbf(ok.X, ok.Y, ok.v, suav=30), ok.v.min() - 5, None)
    PIEZO = np.minimum(PIEZO, TOPO + 2)
    log(f"Piezometría regional: {len(h)} NE en el área, {len(h) - len(hv)} descartados por control, "
        f"{len(P)} puntos tras promediar, {P.outlier.sum()} outliers")

    # ---------------- 2. red de monitoreo en una fecha
    m, _ = dh.monitoreo(x, pz)
    m = en_area(m[m.Z_boca.notna()])
    mf = m[m.fecha == pd.Timestamp(fecha_mon)].copy()
    M = qc_puntos(mf, "carga", piso=4.0)
    okm = M[~M.outlier]
    PIEZO_MON = np.minimum(np.clip(g.rbf(okm.X, okm.Y, okm.v, suav=60, vecinos=30), okm.v.min() - 5, None), TOPO + 2)
    LEJOS_MON = g.lejos(okm.X, okm.Y, 5000)
    # tendencia por pozo (m/año), pozos con ≥ 10 lecturas
    tend = []
    for cod, gg in m.groupby("Cod_UK"):
        if len(gg) >= 10:
            t = (gg.fecha - pd.Timestamp("2000-01-01")).dt.days / 365.25
            tend.append({"Cod_UK": cod, "X": gg.X.iloc[0], "Y": gg.Y.iloc[0], "n": len(gg),
                         "desde": gg.fecha.min().date(), "hasta": gg.fecha.max().date(),
                         "tendencia_m_anio": np.polyfit(t, gg.carga, 1)[0],
                         "amplitud_m": gg.prof_agua.quantile(0.95) - gg.prof_agua.quantile(0.05)})
    tend = pd.DataFrame(tend)
    log(f"Monitoreo {fecha_mon[:7]}: {len(mf)} pozos, {M.outlier.sum()} outliers; tendencia mediana "
        f"{tend.tendencia_m_anio.median():+.3f} m/año ({len(tend)} pozos)")

    # ---------------- 3. napas (horizontes con aporte)
    n = en_area(dh.napas(x, pz))
    n = n[n.Z_boca.notna()].copy()
    n["prof_ini"] = n[["Inicio", "Fin"]].min(axis=1)
    n["prof_mid"] = np.where(n.Inicio.notna() & n.Fin.notna(), (n.Inicio + n.Fin) / 2, n.prof_ini)
    lit = tramos.sort_values(["Cod_UK", "Desde"])

    def formacion_en(cod, z):
        t = lit[(lit.Cod_UK == cod) & (lit.Desde <= z) & (lit.Hasta >= z)]
        return t.Formacion.iloc[0] if len(t) else "Sin litología"
    n["formacion"] = [formacion_en(c, z) for c, z in zip(n.Cod_UK, n.prof_mid)]
    prim = n.sort_values("prof_ini").groupby("Cod_UK").first().reset_index()
    prim["cota_techo_napa"] = prim.Z_boca - prim.prof_ini
    prim["S1_modelo"] = g.en(S1, prim.X, prim.Y)
    prim["dif_S1"] = prim.cota_techo_napa - prim.S1_modelo
    prim_ok = prim[~prim.dudoso & prim.formacion.isin(["Fm Raigon", "Fm Chuy", "Sin litología", "Sin dato"])]
    N = qc_puntos(prim_ok, "cota_techo_napa", piso=4.0)
    okn = N[~N.outlier]
    NAPA_TOP = np.minimum(np.clip(g.rbf(okn.X, okn.Y, okn.v, suav=30), okn.v.min() - 10, None), TOPO)
    log(f"Napas: {len(n)} horizontes en {n.Cod_UK.nunique()} pozos; techo de la 1ª napa en {len(N)} puntos "
        f"({N.outlier.sum()} outliers)")

    # ---------------- 4. derivadas
    PROF_AGUA = TOPO - PIEZO
    SAT = np.clip(np.minimum(PIEZO, S1) - S2, 0, None)
    CONF = PIEZO > S1 + 0.5                         # carga por encima del techo del acuífero: semiconfinado
    dzy, dzx = np.gradient(PIEZO, gy, gx)
    GRAD = np.hypot(dzx, dzy)
    # transmisividad (Logan 1964): T ≈ 1.22 Q/s  (Q en m3/d, s en m)
    hq = dh.nivel_estatico(x, pz)
    hq = en_area(hq[hq.Q_esp.notna() & (hq.Q_esp > 0)])
    hq["T_m2d"] = 1.22 * hq.Q_esp * 24
    hq = hq[(hq.T_m2d > 0.5) & (hq.T_m2d < 5000)]
    TQ = qc_puntos(hq.assign(logT=np.log10(hq.T_m2d)), "logT", piso=0.7)
    okt = TQ[~TQ.outlier]
    LOGT = np.clip(g.rbf(okt.X, okt.Y, okt.v, suav=200), okt.v.min(), okt.v.max())
    log(f"Transmisividad: {len(TQ)} puntos con capacidad específica, mediana {10 ** okt.v.median():.0f} m²/d")

    fuera = MASK | g.lejos(ok.X, ok.Y)
    capas = dict(PIEZO=PIEZO, PIEZO_MON=PIEZO_MON, NAPA_TOP=NAPA_TOP, PROF_AGUA=PROF_AGUA, SAT=SAT,
                 CONF=CONF.astype(float), GRAD=GRAD, LOGT=LOGT, dzx=dzx, dzy=dzy)
    for k in capas:
        capas[k] = np.where(fuera, np.nan, capas[k])
    capas["PIEZO_MON"] = np.where(LEJOS_MON | fuera, np.nan, capas["PIEZO_MON"])
    capas["NAPA_TOP"] = np.where(g.lejos(okn.X, okn.Y) | fuera, np.nan, capas["NAPA_TOP"])
    puntos = dict(ne=P.assign(capa="NE regional"), mon=M.assign(capa=f"Monitoreo {fecha_mon[:7]}"),
                  napa=N.assign(capa="Techo 1ª napa"), T=TQ.assign(capa="Transmisividad (log10 m²/d)"),
                  napas_todas=n, napa_vs_S1=prim, tendencia=tend, descartes_ne=h[h.descarte != ""],
                  monitoreo=m)
    return capas, puntos, g
