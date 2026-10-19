"""Modelo conceptual 2.5D del SAR por formación (mismo método que el notebook modelo_conceptual_SAR)."""
import numpy as np
import pandas as pd
from scipy.interpolate import RBFInterpolator
from scipy.spatial import cKDTree

CELDA = 500            # m
DIST_MAX = 3000        # máscara: distancia máxima a un dato (m)
SUAVIZADO = 20.0
MARGEN_CENSURA = 2.0
UMBRAL_OUTLIER = 3.5
ESP_FB = 30.0          # espesor dibujado de Fray Bentos (base no definida por pozos)
TOL_BOCA = 15.0        # pozos con boca a más de esto de la topografía se excluyen

FORM = {  # nombre en la base -> (etiqueta, color)
    "Actual": ("Suelo / Actual", "#5b3a1e"),
    "Fm Dolores": ("Fm Dolores", "#c9a66b"),
    "Fm Libertad": ("Fm Libertad", "#d98c4a"),
    "Fm Chuy": ("Fm Chuy", "#f2d64b"),
    "Fm Raigon": ("Fm Raigón", "#3b7dd8"),
    "Fm Camacho": ("Fm Camacho", "#5fa35f"),
    "Fm Fray Bentos": ("Fm Fray Bentos", "#e39aa8"),
    "Basamento": ("Basamento", "#a33a3a"),
}
GRIS = "#9a9a9a"
COB = ["Actual", "Fm Dolores", "Fm Libertad"]
ACU = ["Fm Chuy", "Fm Raigon"]
ORDEN = COB + ACU + ["Fm Camacho", "Fm Fray Bentos"]


def _loo(df, kvec=12):
    xy, z = df[["X", "Y"]].values, df["Z"].values
    d, idx = cKDTree(xy).query(xy, k=min(kvec + 1, len(df)))
    w = 1 / np.maximum(d[:, 1:], 1) ** 2
    return z - (w * z[idx[:, 1:]]).sum(1) / w.sum(1)


def _dedup(df, tol=20):
    df = df.copy()
    df["_k"] = (df.X // tol).astype(int).astype(str) + "_" + (df.Y // tol).astype(int).astype(str)
    return df.groupby("_k").agg(Cod_UK=("Cod_UK", "first"), X=("X", "mean"), Y=("Y", "mean"),
                                Z=("Z", "mean")).reset_index(drop=True)


def _outliers(df, piso=3.0):
    df = df.copy()
    df["residuo_LOO"] = _loo(df)
    r = df["residuo_LOO"]
    mad = np.median(np.abs(r - np.median(r))) * 1.4826
    df["outlier"] = np.abs(r) > max(UMBRAL_OUTLIER * mad, piso)
    return df


class Grilla:
    def __init__(self, c):
        self.gx = np.arange(c.X.min() - 2000, c.X.max() + 2000 + CELDA, CELDA)
        self.gy = np.arange(c.Y.min() - 2000, c.Y.max() + 2000 + CELDA, CELDA)
        self.GX, self.GY = np.meshgrid(self.gx, self.gy)
        self.XY = np.column_stack([self.GX.ravel(), self.GY.ravel()])

    def valor_en(self, G, x, y):
        i = np.clip(np.round((np.asarray(y) - self.gy[0]) / CELDA).astype(int), 0, len(self.gy) - 1)
        j = np.clip(np.round((np.asarray(x) - self.gx[0]) / CELDA).astype(int), 0, len(self.gx) - 1)
        return G[i, j]

    def interpolar(self, df, censura=None, iteraciones=3, suav=SUAVIZADO):
        ok = df[~df.outlier][["X", "Y", "Z"]]
        zlo, zhi = ok.Z.min() - 10, ok.Z.max() + 5
        datos = ok.copy()
        for it in range(iteraciones + 1):
            rbf = RBFInterpolator(datos[["X", "Y"]].values, datos["Z"].values, kernel="linear",
                                  smoothing=suav, neighbors=40)
            G = np.clip(rbf(self.XY).reshape(self.GX.shape), zlo, zhi)
            if censura is None or censura.empty or it == iteraciones:
                break
            zc = self.valor_en(G, censura.X.values, censura.Y.values)
            viol = censura[zc > censura.Z_max.values]
            if viol.empty:
                break
            extra = pd.DataFrame({"X": viol.X, "Y": viol.Y, "Z": viol.Z_max - MARGEN_CENSURA})
            datos = pd.concat([datos, extra]).drop_duplicates(subset=["X", "Y"], keep="last")
            zlo = min(zlo, extra.Z.min() - 5)
        return G

    def espesor(self, x, y, v, suav=50.0):
        if len(v) < 3:
            return np.full(self.GX.shape, float(np.nanmean(v)) if len(v) else 0.0)
        rbf = RBFInterpolator(np.column_stack([x, y]), v, kernel="linear", smoothing=suav, neighbors=40)
        return np.clip(rbf(self.XY).reshape(self.GX.shape), 0, None)

    def mascara(self, df, dmax=DIST_MAX):
        d, _ = cKDTree(df[["X", "Y"]].values).query(self.XY)
        return (d > dmax).reshape(self.GX.shape)


def construir(contactos, no_alcanzados, topo, tramos, log=print):
    c, k = contactos, no_alcanzados
    t = _outliers(_dedup(topo), piso=10.0)
    S = {s: _outliers(_dedup(c[c.Superficie == s])) for s in ["S1_techo_acuifero", "S2_base_acuifero"]}
    g = Grilla(c)
    TOPO = g.interpolar(t)
    S1 = np.minimum(g.interpolar(S["S1_techo_acuifero"], censura=k[k.Superficie == "S1_techo_acuifero"]), TOPO)
    S2 = np.minimum(g.interpolar(S["S2_base_acuifero"], censura=k[k.Superficie == "S2_base_acuifero"]), S1)
    MASK = g.mascara(pd.concat([S["S1_techo_acuifero"], S["S2_base_acuifero"]]))
    log(f"Outliers: topografía {t.outlier.sum()}, techo acuífero {S['S1_techo_acuifero'].outlier.sum()}, "
        f"base acuífero {S['S2_base_acuifero'].outlier.sum()}")

    # pozos válidos: dentro del área y boca coherente con la topografía
    tr = tramos.copy()
    zt = g.valor_en(TOPO, tr.X.values, tr.Y.values)
    dentro = ~g.valor_en(MASK, tr.X.values, tr.Y.values).astype(bool)
    boca = tr.groupby("Cod_UK").Cota_desde.transform("max")
    ok = dentro & (np.abs(boca - zt) < TOL_BOCA)
    excluidos = sorted(tr.loc[~ok & dentro, "Cod_UK"].unique())
    log(f"Pozos excluidos (boca > {TOL_BOCA:g} m de la topografía): {excluidos}")
    tr = tr[ok].copy()

    def espesores(forms, pozos):
        filas = []
        for cod, gg in tr[tr.Cod_UK.isin(pozos)].groupby("Cod_UK"):
            sub = gg[gg.Formacion.isin(forms)]
            filas.append({"X": gg.X.iloc[0], "Y": gg.Y.iloc[0],
                          **{f: (sub[sub.Formacion == f].Hasta - sub[sub.Formacion == f].Desde).sum() for f in forms}})
        return pd.DataFrame(filas)

    def repartir(forms, ed, total):
        th = np.stack([g.espesor(ed.X.values, ed.Y.values, ed[f].values) for f in forms])
        s = th.sum(0)
        frac = np.where(s[None] > 0.1, th / np.maximum(s[None], 1e-6), 0)
        frac[-1] = np.where(s > 0.1, frac[-1], 1.0)
        return frac * total[None]

    th_cob = repartir(COB, espesores(COB, set(c[c.Superficie == "S1_techo_acuifero"].Cod_UK)), np.maximum(TOPO - S1, 0))
    th_acu = repartir(ACU, espesores(ACU, set(c[c.Superficie == "S2_base_acuifero"].Cod_UK)), np.maximum(S1 - S2, 0))

    cam = []
    for cod, gg in tr.groupby("Cod_UK"):
        gg = gg.sort_values("Desde")
        f = gg.Formacion.fillna("ND").tolist()
        if "Fm Camacho" in f:
            sub = gg[gg.Formacion == "Fm Camacho"]
            cam.append({"X": gg.X.iloc[0], "Y": gg.Y.iloc[0], "e": (sub.Hasta - sub.Desde).sum()})
        elif "Fm Fray Bentos" in f and any(x in f for x in ACU):
            cam.append({"X": gg.X.iloc[0], "Y": gg.Y.iloc[0], "e": 0.0})
    cam = pd.DataFrame(cam)
    TH_CAM = g.espesor(cam.X.values, cam.Y.values, cam.e.values)

    techos, z = {}, TOPO.copy()
    for f, th in list(zip(COB, th_cob)) + list(zip(ACU, th_acu)):
        techos[f] = z.copy()
        z = z - th
    techos["Fm Camacho"] = S2.copy()
    techos["Fm Fray Bentos"] = S2 - TH_CAM
    BASE = techos["Fm Fray Bentos"] - ESP_FB
    SUP = np.stack([techos[f] for f in ORDEN] + [BASE])  # (8, ny, nx): techo de cada formación + base
    return dict(gx=g.gx, gy=g.gy, MASK=MASK, SUP=SUP, TOPO=TOPO, tramos=tr, excluidos=excluidos,
                outliers=pd.concat([d[d.outlier].assign(Superficie=s) for s, d in
                                    [("Topografía", t), *S.items()]]))
