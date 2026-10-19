"""Lectura y control de calidad de los datos hidráulicos de la base acotada.

- Hoja «Historico niveles»: red de monitoreo (profundidad al agua, m bajo boca). Los encabezados de fecha vienen
  mal convertidos por Excel ("Ago-01" -> 2025-08-01): el día es el año y el año es falso. Se reconstruye la fecha
  respetando el orden cronológico de las columnas.
- Hoja «Hidráulica»: nivel estático (NE) medido en la perforación o en relevamientos, la mayoría sin fecha.
- Hoja «Ubic. Napas_m»: profundidad de los horizontes con aporte de agua (napas) declarados por el perforista.
- Hoja «Filtros_m»: tramos filtrados.
"""
import re
import datetime as dt
import numpy as np
import pandas as pd

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "sep": 9,
         "sept": 9, "oct": 10, "nov": 11, "dic": 12}


def _candidatas(c):
    """Fechas posibles (primer día del mes) para un encabezado de columna."""
    if isinstance(c, (dt.datetime, pd.Timestamp)):
        out = [dt.date(2000 + c.day, c.month, 1)] if c.day <= 30 else []
        if c.day == 1:
            out.append(dt.date(c.year, c.month, 1))
        if c.year <= 2023 and c.day > 1:          # fecha real con día (p. ej. 2021-10-06)
            out.append(dt.date(c.year, c.month, 1))
        return out
    s = str(c).lower().strip().replace(".", " ").replace("_", " ").replace("-", " ")
    m = re.match(r"([a-z]+)\s*(\d{2,4})$", s.replace(" ", ""))
    if not m or m.group(1)[:4].rstrip() not in MESES and m.group(1)[:3] not in MESES:
        return []
    mes = MESES.get(m.group(1)[:4], MESES.get(m.group(1)[:3]))
    a = int(m.group(2))
    a = a + (1900 if a > 50 else 2000) if a < 100 else a
    return [dt.date(a, mes, 1)]


def fechas_monitoreo(cols):
    """Reconstruye la fecha de cada columna eligiendo, entre las candidatas, la primera >= la anterior."""
    out, prev = {}, dt.date(1980, 1, 1)
    for c in cols:
        cand = sorted(x for x in _candidatas(c) if x >= prev)
        if cand:
            out[c] = cand[0]
            prev = cand[0]
    return out


def leer(base):
    x = pd.ExcelFile(base)
    pz = x.parse("Pozos").rename(columns={"X-UTM": "X", "Y-UTM": "Y", "Cod UK": "Cod_UK", "Z_boca (m)": "Z_boca",
                                          "Prof. Total": "Prof_total", "Fuente BD": "Fuente"})
    pz = pz[["Cod_UK", "X", "Y", "Z_boca", "Fuente_Z", "Prof_total", "Departamento", "Localidad", "Fuente"]]
    pz = pz.dropna(subset=["X", "Y"]).drop_duplicates("Cod_UK")
    return x, pz


def monitoreo(x, pz):
    hn = x.parse("Historico niveles").rename(columns={"Cod UK": "Cod_UK"})
    fcols = [c for c in hn.columns if c not in ("Cod_UK", "Propietario", "Z_m") and not str(c).startswith("Fuente")]
    fechas = fechas_monitoreo(fcols)
    largo = hn.melt(id_vars=["Cod_UK", "Propietario"], value_vars=list(fechas), var_name="col", value_name="prof_agua")
    largo["prof_agua"] = pd.to_numeric(largo.prof_agua, errors="coerce")
    largo = largo.dropna(subset=["prof_agua"])
    largo["fecha"] = pd.to_datetime(largo.col.map(fechas))
    largo = largo.merge(pz[["Cod_UK", "X", "Y", "Z_boca", "Fuente_Z", "Departamento"]], on="Cod_UK", how="left")
    largo["carga"] = largo.Z_boca - largo.prof_agua
    return largo.drop(columns="col"), fechas


def nivel_estatico(x, pz):
    h = x.parse("Hidráulica").rename(columns={"Cod UK": "Cod_UK"})
    h["NE"] = pd.to_numeric(h.NE_m, errors="coerce")
    h["surgente"] = h.NE_m.astype(str).str.contains("surg", case=False)
    h.loc[h.surgente, "NE"] = 0.0
    h["ND"] = pd.to_numeric(h["ND-m"], errors="coerce")
    h["Caudal"] = pd.to_numeric(h.Caudal, errors="coerce")
    h["anio"] = pd.to_numeric(h["Año"], errors="coerce")
    f = pd.to_datetime(h.Fecha, errors="coerce")
    h.loc[h.anio.isna() & f.notna(), "anio"] = f.dt.year
    h = h.merge(pz, on="Cod_UK", how="left")
    h["carga"] = h.Z_boca - h.NE
    h["descenso"] = h.ND - h.NE
    h["Q_esp"] = h.Caudal / h.descenso.where(h.descenso > 0.05)
    return h


def napas(x, pz, hoja="Ubic. Napas_m"):
    n = x.parse(hoja).rename(columns={"Cod UK": "Cod_UK", "Inicio Acotado": "Z_ini", "Fin Acotado": "Z_fin"})
    n = n[n.Inicio.notna() | n.Fin.notna()].copy()
    n["dudoso"] = n.Comentarios.astype(str).str.contains("no se sabe", case=False)
    n = n.merge(pz[["Cod_UK", "X", "Y", "Z_boca", "Prof_total"]], on="Cod_UK", how="left")
    return n
