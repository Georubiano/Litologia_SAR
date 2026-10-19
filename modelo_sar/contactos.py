"""Lectura de la base acotada y extracción de contactos hidroestratigráficos (zona SAR, San José)."""
import numpy as np
import pandas as pd

Y_MIN, Y_MAX = 6_100_000, 6_250_000  # zona sur (SAR)

UNIDAD = {
    "Actual": "U1_Cobertura", "Fm Dolores": "U1_Cobertura", "Fm Libertad": "U1_Cobertura",
    "Fm Raigon": "U2_Acuifero", "Fm Chuy": "U2_Acuifero",
    "Fm Camacho": "U3_Camacho", "Fm Fray Bentos": "U4_FrayBentos", "Basamento": "U5_Basamento",
}
ORDEN_U = {"U1_Cobertura": 1, "U2_Acuifero": 2, "U3_Camacho": 3, "U4_FrayBentos": 4, "U5_Basamento": 5}
SUPERFICIES = {  # contacto = base de la unidad superior
    ("U1_Cobertura", "U2_Acuifero"): "S1_techo_acuifero",
    ("U2_Acuifero", "U3_Camacho"): "S2_base_acuifero",
    ("U2_Acuifero", "U4_FrayBentos"): "S2_base_acuifero",
    ("U2_Acuifero", "U5_Basamento"): "S2_base_acuifero",
    ("U3_Camacho", "U4_FrayBentos"): "S3_base_Camacho",
    ("U3_Camacho", "U5_Basamento"): "S3_base_Camacho",
}


def leer_base(path):
    """Devuelve (pozos, litologia) de la zona SAR desde la base acotada (.xlsx)."""
    p = pd.read_excel(path, "Pozos")
    l = pd.read_excel(path, "Litología")
    l = l[l["Cod UK"].notna()].drop(columns=["Z_boca (m)"], errors="ignore")
    l = l.merge(p[["Cod UK", "X-UTM", "Y-UTM", "Z_boca (m)", "Fuente_Z", "Fuente BD"]].rename(
        columns={"Fuente BD": "Fuente_pozo"}), on="Cod UK", how="left")
    l = l[(l["Y-UTM"] > Y_MIN) & (l["Y-UTM"] < Y_MAX)]
    l = l[(l["Version_tramos"].isna()) | (l["Version_tramos"] == 1)]
    l = l.dropna(subset=["Inicio Acotado", "Fin Acotado"])
    pz = p[(p["Y-UTM"] > Y_MIN) & (p["Y-UTM"] < Y_MAX)].copy()
    return pz, l


def tabla_tramos(l):
    t = l[["Cod UK", "X-UTM", "Y-UTM", "Z_boca (m)", "Inicio", "Fin", "Inicio Acotado", "Fin Acotado",
           "Formacion_unificada", "Litologia", "Litologia_simplificada", "Fuente_pozo"]].copy()
    t.columns = ["Cod_UK", "X", "Y", "Z_boca", "Desde", "Hasta", "Cota_desde", "Cota_hasta", "Formacion",
                 "Litologia", "Litologia_simpl", "Fuente"]
    t["Desde"] = pd.to_numeric(t["Desde"], errors="coerce")
    t["Hasta"] = pd.to_numeric(t["Hasta"], errors="coerce")
    return t.dropna(subset=["Desde", "Hasta"]).reset_index(drop=True)


def extraer_contactos(l):
    zona = l.copy()
    zona["Unidad"] = zona["Formacion_unificada"].map(UNIDAD)
    zona = zona.sort_values(["Cod UK", "Inicio"])
    contactos, censurados, problemas = [], [], []
    for cod, g in zona.groupby("Cod UK"):
        x, y, zb = g["X-UTM"].iloc[0], g["Y-UTM"].iloc[0], g["Z_boca (m)"].iloc[0]
        fondo = g["Fin Acotado"].min()
        bloques = []
        for _, t in g.iterrows():
            u = t["Unidad"] if isinstance(t["Unidad"], str) else "ND"
            if bloques and bloques[-1][0] == u and abs(bloques[-1][2] - t["Inicio Acotado"]) < 0.5:
                bloques[-1][2] = t["Fin Acotado"]
            else:
                bloques.append([u, t["Inicio Acotado"], t["Fin Acotado"]])
        base = dict(Cod_UK=cod, X=x, Y=y, Z_boca=zb)
        if bloques[0][0] == "U2_Acuifero":
            contactos.append({**base, "Superficie": "S1_techo_acuifero", "Z": zb, "Tipo": "aflora (sin cobertura)"})
        us = [b[0] for b in bloques if b[0] != "ND"]
        if any(ORDEN_U[a] > ORDEN_U[b] for a, b in zip(us, us[1:])):
            problemas.append({**base, "Problema": "orden estratigráfico invertido", "Secuencia": " > ".join(us)})
            continue
        for (u0, _, f0), (u1, _, _) in zip(bloques, bloques[1:]):
            if (u0, u1) in SUPERFICIES:
                contactos.append({**base, "Superficie": SUPERFICIES[(u0, u1)], "Z": f0, "Tipo": "contacto"})
        ult = bloques[-1][0]
        if ult in ("U1_Cobertura", "U2_Acuifero", "U3_Camacho"):
            sup = {"U1_Cobertura": "S1_techo_acuifero", "U2_Acuifero": "S2_base_acuifero",
                   "U3_Camacho": "S3_base_Camacho"}[ult]
            censurados.append({**base, "Superficie": sup, "Z_max": fondo})
    return pd.DataFrame(contactos), pd.DataFrame(censurados), pd.DataFrame(problemas)


def topografia(pz):
    t = pz.copy()
    t["Z"] = t["Z MDT"].where(t["Z MDT"].notna(), t["Z_boca (m)"])
    t = t[t["Z"].notna()][["Cod UK", "X-UTM", "Y-UTM", "Z"]]
    t.columns = ["Cod_UK", "X", "Y", "Z"]
    return t.reset_index(drop=True)
