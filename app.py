import os, json, io
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, jsonify, send_file, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "agronat_dev_secret_2026")

# ── DATABASE ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_db():
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url)
        conn.autocommit = False
        return conn, "pg"
    else:
        import sqlite3
        os.makedirs("instance", exist_ok=True)
        conn = sqlite3.connect("instance/agronat.db")
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

def query(sql, params=(), one=False, commit=False):
    conn, mode = get_db()
    try:
        if mode == "pg":
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Adapt ? to %s for postgres
            sql_pg = sql.replace("?", "%s")
            cur.execute(sql_pg, params)
            if commit:
                conn.commit()
                return None
            rows = cur.fetchall()
            return (dict(rows[0]) if rows else None) if one else [dict(r) for r in rows]
        else:
            cur = conn.execute(sql, params)
            if commit:
                conn.commit()
                return None
            rows = cur.fetchall()
            if one:
                return dict(rows[0]) if rows else None
            return [dict(r) for r in rows]
    finally:
        conn.close()

def executescript(sql):
    conn, mode = get_db()
    try:
        if mode == "pg":
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
        else:
            conn.executescript(sql)
            conn.commit()
    finally:
        conn.close()

def init_db():
    if DATABASE_URL:
        sql = """
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nombre TEXT NOT NULL,
            activo INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS registros (
            id SERIAL PRIMARY KEY,
            equipo TEXT NOT NULL,
            frecuencia TEXT NOT NULL,
            turno TEXT,
            fecha_registro TEXT NOT NULL,
            usuario_id INTEGER NOT NULL,
            datos TEXT NOT NULL,
            observaciones TEXT
        );
        """
    else:
        sql = """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nombre TEXT NOT NULL,
            activo INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS registros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equipo TEXT NOT NULL,
            frecuencia TEXT NOT NULL,
            turno TEXT,
            fecha_registro TEXT NOT NULL,
            usuario_id INTEGER NOT NULL,
            datos TEXT NOT NULL,
            observaciones TEXT
        );
        """
    executescript(sql)
    # Default admin
    existing = query("SELECT id FROM usuarios WHERE username=?", ("admin",), one=True)
    if not existing:
        query("INSERT INTO usuarios (username, password_hash, nombre) VALUES (?,?,?)",
              ("admin", generate_password_hash("agronat2026"), "Administrador"), commit=True)

# ── CHECKLISTS DATA ───────────────────────────────────────────────────────────
CHECKLISTS = {
    "Extrusora": {
        "Diario": {
            "subtitulo": "Completar al inicio de cada turno (cada 8 hs)",
            "columnas": ["turno_dia", "turno_noche"], "col_labels": ["Turno Día", "Turno Noche"],
            "tareas": [
                {"id":"e_d_01","tarea":"Inspección visual general — estado exterior, paneles y accesos"},
                {"id":"e_d_02","tarea":"Nivel de aceite — caja de engranajes y reductor principal","obs":"Registrar en visor"},
                {"id":"e_d_03","tarea":"Temperatura de cámara de extrusión — registrar valor en °C"},
                {"id":"e_d_04","tarea":"Temperatura del expeller a la salida — registrar valor en °C"},
                {"id":"e_d_05","tarea":"Granulometría del expeller — muestra visual o tamiz"},
                {"id":"e_d_06","tarea":"Ruidos anormales en gusano, motor o transmisión"},
                {"id":"e_d_07","tarea":"Vibración excesiva en estructura o bancada"},
                {"id":"e_d_08","tarea":"Control de canilla de alimentación — flujo y apertura correcta"},
                {"id":"e_d_09","tarea":"Presión de vapor / agua — dentro de rango operativo"},
                {"id":"e_d_10","tarea":"Temperatura de rodamientos del motor — tacto / termómetro IR"},
                {"id":"e_d_11","tarea":"Pérdidas de aceite en caja de engranajes o sellos"},
                {"id":"e_d_12","tarea":"Amperaje del motor principal — dentro de rango nominal","obs":"Registrar valor"},
            ]
        },
        "Semanal": {
            "subtitulo": "Realizar durante la parada de fin de semana (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"e_s_01","tarea":"Desarme, control y limpieza de canilla de alimentación"},
                {"id":"e_s_02","tarea":"Revisión de correas — estado, grietas y desgaste lateral"},
                {"id":"e_s_03","tarea":"Tensión de correas — flecha máx. 1% entre centros; ajustar si corresponde"},
                {"id":"e_s_04","tarea":"Limpieza de imán de la línea de alimentación"},
                {"id":"e_s_05","tarea":"Engrase de rodamientos de motor y transmisión — grasa NLGI 2"},
                {"id":"e_s_06","tarea":"Limpieza general de extrusora y bancada"},
                {"id":"e_s_07","tarea":"Revisión visual de gusano — desgaste de hélice, estado de matriz"},
                {"id":"e_s_08","tarea":"Control de apriete de tornillos de carcasa y bridas"},
                {"id":"e_s_09","tarea":"Revisión de sellos de eje — pérdidas de aceite o producto"},
                {"id":"e_s_10","tarea":"Nivel de aceite caja de engranajes — completar si corresponde","obs":"Ver visor"},
            ]
        },
        "Mensual": {
            "subtitulo": "Realizar durante parada programada (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"e_m_01","tarea":"Cambio de aceite caja de engranajes — ISO VG 220 o según fabricante","obs":"Registrar fecha y litros"},
                {"id":"e_m_02","tarea":"Revisión completa de rodamientos — juego, temperatura y ruido"},
                {"id":"e_m_03","tarea":"Inspección de gusano — medir desgaste de hélice con calibre","obs":"Registrar medidas"},
                {"id":"e_m_04","tarea":"Inspección de matriz — desgaste de orificios"},
                {"id":"e_m_05","tarea":"Revisión de alineación motor / transmisión — verificar acoples"},
                {"id":"e_m_06","tarea":"Calibración de termocuplas de cámara — contrastar con termómetro patrón"},
                {"id":"e_m_07","tarea":"Revisión de correas — reemplazar si tienen grietas o desgaste lateral"},
                {"id":"e_m_08","tarea":"Control de tablero eléctrico — conexiones, fusibles y térmicas"},
                {"id":"e_m_09","tarea":"Inspección visual de estructura — fisuras, soldaduras, soportes"},
                {"id":"e_m_10","tarea":"Prueba de arranque en vacío — temperatura, ruido, amperaje"},
            ]
        },
        "Pre-Zafra": {
            "subtitulo": "Realizar antes del inicio de campaña — revisión completa con equipo detenido",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"e_z_01","tarea":"Cambio de aceite caja de engranajes (si no se realizó en último mensual)"},
                {"id":"e_z_02","tarea":"Reengrase completo de todos los rodamientos"},
                {"id":"e_z_03","tarea":"Reemplazo de correas si presentan grietas, estiramiento o desgaste"},
                {"id":"e_z_04","tarea":"Inspección y medición de gusano — reemplazar si desgaste supera límite","obs":"Registrar medidas"},
                {"id":"e_z_05","tarea":"Inspección de matriz — reemplazar si orificios están deformados"},
                {"id":"e_z_06","tarea":"Limpieza profunda interior: cámara, gusano, canilla y bancada"},
                {"id":"e_z_07","tarea":"Calibración de todas las termocuplas de cámara","obs":"Registrar valores pre/post"},
                {"id":"e_z_08","tarea":"Revisión de alineación completa motor / reductor / gusano"},
                {"id":"e_z_09","tarea":"Prueba de encendido en vacío — verificar temperatura y amperaje"},
                {"id":"e_z_10","tarea":"Tablero eléctrico — conexiones, fusibles y térmicas calibradas"},
                {"id":"e_z_11","tarea":"Prueba de alarmas de temperatura y sobrecarga"},
                {"id":"e_z_12","tarea":"Estructura: soldaduras, soportes y fijaciones al piso"},
            ]
        },
    },
    "Prensas de Tornillo": {
        "Diario": {
            "subtitulo": "Completar al inicio de cada turno (cada 8 hs)",
            "columnas": ["turno_dia","turno_noche"], "col_labels": ["Turno Día","Turno Noche"],
            "tareas": [
                {"id":"p_d_01","tarea":"Inspección visual general — estado exterior y paneles"},
                {"id":"p_d_02","tarea":"Nivel de aceite de caja de engranajes — visor / varilla"},
                {"id":"p_d_03","tarea":"Revisión visual de posibles pérdidas de aceite en caja de engranajes"},
                {"id":"p_d_04","tarea":"Nivel de aceite de reductores de tornillos — visor"},
                {"id":"p_d_05","tarea":"Temperatura de rodamientos del motor — tacto / termómetro IR"},
                {"id":"p_d_06","tarea":"Ruidos anormales en tornillo, caja o transmisión"},
                {"id":"p_d_07","tarea":"Vibración excesiva en estructura o bancada"},
                {"id":"p_d_08","tarea":"Presión de aceite de prensa — dentro de rango operativo"},
                {"id":"p_d_09","tarea":"Temperatura del expeller a la salida — registrar valor"},
                {"id":"p_d_10","tarea":"Amperaje del motor principal — dentro de rango nominal","obs":"Registrar valor"},
                {"id":"p_d_11","tarea":"Pérdidas de aceite en sellos o bridas"},
            ]
        },
        "Semanal": {
            "subtitulo": "Realizar durante la parada de fin de semana (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"p_s_01","tarea":"Nivel de aceite caja de engranajes — completar si corresponde"},
                {"id":"p_s_02","tarea":"Engrase de rodamientos — Prensa 1","obs":"Grasa NLGI 2"},
                {"id":"p_s_03","tarea":"Engrase de rodamientos — Prensa 2","obs":"Grasa NLGI 2"},
                {"id":"p_s_04","tarea":"Engrase de rodamientos — Prensa 3","obs":"Grasa NLGI 2"},
                {"id":"p_s_05","tarea":"Engrase de rodamientos — Prensa 4","obs":"Grasa NLGI 2"},
                {"id":"p_s_06","tarea":"Engrase de rodamientos — Prensa 5","obs":"Grasa NLGI 2"},
                {"id":"p_s_07","tarea":"Engrase de rodamientos — Prensa 6","obs":"Grasa NLGI 2"},
                {"id":"p_s_08","tarea":"Tensión de correas — verificar y ajustar; flecha máx. 1% entre centros"},
                {"id":"p_s_09","tarea":"Limpieza de canastos de prensa — sin obstrucciones"},
                {"id":"p_s_10","tarea":"Limpieza general de prensa y bancada"},
                {"id":"p_s_11","tarea":"Revisión visual de tornillo — desgaste de hélice y caja"},
                {"id":"p_s_12","tarea":"Control de apriete de tornillos de carcasa y bridas"},
            ]
        },
        "Mensual": {
            "subtitulo": "Realizar durante parada programada (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"p_m_01","tarea":"Cambio de aceite — Prensa 1 (ISO VG 220 o según fabricante)","obs":"Registrar fecha y litros"},
                {"id":"p_m_02","tarea":"Cambio de aceite — Prensa 2","obs":"Registrar fecha y litros"},
                {"id":"p_m_03","tarea":"Cambio de aceite — Prensa 3","obs":"Registrar fecha y litros"},
                {"id":"p_m_04","tarea":"Cambio de aceite — Prensa 4","obs":"Registrar fecha y litros"},
                {"id":"p_m_05","tarea":"Cambio de aceite — Prensa 5","obs":"Registrar fecha y litros"},
                {"id":"p_m_06","tarea":"Cambio de aceite — Prensa 6","obs":"Registrar fecha y litros"},
                {"id":"p_m_07","tarea":"Revisión completa de rodamientos — juego, temperatura y ruido"},
                {"id":"p_m_08","tarea":"Inspección de tornillo y caja — medir desgaste con calibre","obs":"Registrar medidas"},
                {"id":"p_m_09","tarea":"Revisión de correas — reemplazar si presentan grietas o desgaste"},
                {"id":"p_m_10","tarea":"Revisión de alineación motor / reductor / tornillo"},
                {"id":"p_m_11","tarea":"Tablero eléctrico — conexiones, fusibles y térmicas"},
                {"id":"p_m_12","tarea":"Inspección visual de estructura — fisuras, soldaduras, soportes"},
            ]
        },
    },
    "Elevadores de Cangilones": {
        "Diario": {
            "subtitulo": "Completar al inicio de cada turno (cada 8 hs)",
            "columnas": ["turno_dia","turno_noche"], "col_labels": ["Turno Día","Turno Noche"],
            "tareas": [
                {"id":"el_d_01","tarea":"Inspección visual general — carcasa, tapas de inspección y accesos"},
                {"id":"el_d_02","tarea":"Ruidos anormales en banda, cangilones o tambores"},
                {"id":"el_d_03","tarea":"Vibración excesiva en estructura o cabezal"},
                {"id":"el_d_04","tarea":"Temperatura de rodamientos del tambor superior e inferior — IR"},
                {"id":"el_d_05","tarea":"Nivel de aceite del reductor — visor / varilla"},
                {"id":"el_d_06","tarea":"Pérdidas de grano visibles en carcasa, pie o cabeza"},
                {"id":"el_d_07","tarea":"Tensión de banda — verificar visualmente desde ventana de inspección"},
                {"id":"el_d_08","tarea":"Amperaje del motor — dentro de rango nominal","obs":"Registrar valor"},
                {"id":"el_d_09","tarea":"Estado de dispositivo anti-retorno — funciona correctamente"},
                {"id":"el_d_10","tarea":"Temperatura del motor — tacto / termómetro IR"},
            ]
        },
        "Quincenal": {
            "subtitulo": "Realizar cada 15 días durante parada (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"el_q_01","tarea":"Engrase de rodamientos de tambor superior e inferior — grasa NLGI 2"},
                {"id":"el_q_02","tarea":"Engrase de tornillos tensores de banda"},
                {"id":"el_q_03","tarea":"Revisión de tensión de banda — ajustar tensores si corresponde"},
                {"id":"el_q_04","tarea":"Inspección visual de cangilones — rotos, flojos o deformados","obs":"Reemplazar dañados"},
                {"id":"el_q_05","tarea":"Inspección de banda — cortes, deshilachado o empalme en mal estado"},
                {"id":"el_q_06","tarea":"Revisión de pérdidas de grano — sellar si corresponde"},
                {"id":"el_q_07","tarea":"Limpieza general de pie del elevador — sin acumulación de grano","obs":"Riesgo de incendio"},
                {"id":"el_q_08","tarea":"Nivel de aceite del reductor — completar si corresponde"},
                {"id":"el_q_09","tarea":"Alineación de banda en tambores — sin desplazamiento lateral"},
            ]
        },
        "Mensual": {
            "subtitulo": "Realizar durante parada programada (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"el_m_01","tarea":"Cambio de aceite del reductor — ISO VG 220 o según fabricante","obs":"Registrar fecha y litros"},
                {"id":"el_m_02","tarea":"Revisión completa de rodamientos — juego, temperatura y ruido"},
                {"id":"el_m_03","tarea":"Inspección detallada de todos los cangilones — tornillos de fijación","obs":"Torquear los flojos"},
                {"id":"el_m_04","tarea":"Inspección de empalme de banda — estado de grapas o vulcanizado"},
                {"id":"el_m_05","tarea":"Revisión de alineación de tambores superior e inferior"},
                {"id":"el_m_06","tarea":"Inspección de dispositivo anti-retorno — limpieza y funcionalidad"},
                {"id":"el_m_07","tarea":"Control de apriete de tensores de banda"},
                {"id":"el_m_08","tarea":"Revisión de motor eléctrico — temperatura, ruido y conexiones"},
                {"id":"el_m_09","tarea":"Limpieza completa interior de carcasa — pie y cabeza","obs":"Sin acumulación de polvo"},
                {"id":"el_m_10","tarea":"Tablero eléctrico — conexiones, fusibles y protecciones"},
            ]
        },
        "Pre-Zafra": {
            "subtitulo": "Realizar antes del inicio de campaña — revisión completa con equipo detenido",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"el_z_01","tarea":"Cambio de aceite del reductor (si no se realizó en último mensual)"},
                {"id":"el_z_02","tarea":"Reengrase completo de rodamientos de ambos tambores"},
                {"id":"el_z_03","tarea":"Inspección 100% de cangilones — reemplazar rotos o deformados"},
                {"id":"el_z_04","tarea":"Inspección de banda completa — estado, cortes y empalme"},
                {"id":"el_z_05","tarea":"Ajuste de tensión de banda — verificar con tensiómetro si disponible"},
                {"id":"el_z_06","tarea":"Alineación de banda en tambores superior e inferior"},
                {"id":"el_z_07","tarea":"Limpieza profunda interior: pie, carcasa y cabeza","obs":"Sin acumulación — riesgo incendio"},
                {"id":"el_z_08","tarea":"Revisión de dispositivo anti-retorno — reemplazar si hay desgaste"},
                {"id":"el_z_09","tarea":"Prueba de arranque en vacío — ruido, vibración y amperaje"},
                {"id":"el_z_10","tarea":"Tablero eléctrico — conexiones, fusibles y térmicas calibradas"},
                {"id":"el_z_11","tarea":"Prueba de alarmas de sobrecarga y anti-retorno"},
                {"id":"el_z_12","tarea":"Estructura: escaleras, pasarelas, soportes y fijaciones"},
            ]
        },
    },
    "Sinfines": {
        "Diario": {
            "subtitulo": "Completar al inicio de cada turno (cada 8 hs)",
            "columnas": ["turno_dia","turno_noche"], "col_labels": ["Turno Día","Turno Noche"],
            "tareas": [
                {"id":"sf_d_01","tarea":"Inspección visual general — carcasa, tapas y soportes"},
                {"id":"sf_d_02","tarea":"Ruidos anormales en tornillo, rodamientos o transmisión"},
                {"id":"sf_d_03","tarea":"Vibración excesiva en estructura o soportes"},
                {"id":"sf_d_04","tarea":"Temperatura de rodamientos — tacto / termómetro IR"},
                {"id":"sf_d_05","tarea":"Nivel de aceite del reductor — visor / varilla"},
                {"id":"sf_d_06","tarea":"Pérdidas de grano visibles en juntas o tapas"},
                {"id":"sf_d_07","tarea":"Amperaje del motor — dentro de rango nominal","obs":"Registrar valor"},
                {"id":"sf_d_08","tarea":"Estado de sellos extremos — sin pérdidas de polvo o producto"},
            ]
        },
        "Semanal": {
            "subtitulo": "Realizar durante la parada de fin de semana (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"sf_s_01","tarea":"Nivel de aceite de reductores — verificar y completar si corresponde"},
                {"id":"sf_s_02","tarea":"Engrase de todos los rodamientos — grasa NLGI 2"},
                {"id":"sf_s_03","tarea":"Revisión visual de estado de tornillo — desgaste de hélice"},
                {"id":"sf_s_04","tarea":"Limpieza completa de carcasa, tapas y zona de descarga"},
                {"id":"sf_s_05","tarea":"Control de apriete de tornillos de tapa y bridas"},
                {"id":"sf_s_06","tarea":"Revisión de sellos extremos — pérdidas de polvo o producto"},
                {"id":"sf_s_07","tarea":"Revisión de transmisión — correas o cadenas, tensión y estado"},
            ]
        },
        "Mensual": {
            "subtitulo": "Realizar durante parada programada (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"sf_m_01","tarea":"Cambio de aceite de reductores — ISO VG 220 o según fabricante","obs":"Registrar fecha y litros"},
                {"id":"sf_m_02","tarea":"Revisión completa de rodamientos — juego, temperatura y ruido"},
                {"id":"sf_m_03","tarea":"Inspección de tornillo — medir desgaste de hélice con calibre","obs":"Registrar medidas"},
                {"id":"sf_m_04","tarea":"Revisión de correas / cadenas — reemplazar si hay desgaste o grietas"},
                {"id":"sf_m_05","tarea":"Revisión de alineación motor / reductor / eje de tornillo"},
                {"id":"sf_m_06","tarea":"Inspección de carcasa — fisuras, corrosión o deformaciones"},
                {"id":"sf_m_07","tarea":"Revisión de sellos — reemplazar si presentan pérdidas"},
                {"id":"sf_m_08","tarea":"Tablero eléctrico — conexiones, fusibles y térmicas"},
            ]
        },
    },
    "Bombas de Aceite": {
        "Diario": {
            "subtitulo": "Completar al inicio de cada turno (cada 8 hs)",
            "columnas": ["turno_dia","turno_noche"], "col_labels": ["Turno Día","Turno Noche"],
            "tareas": [
                {"id":"b_d_01","tarea":"Inspección visual general — estado exterior, bridas y conexiones"},
                {"id":"b_d_02","tarea":"Revisión visual de posibles pérdidas de aceite en cuerpo y bridas"},
                {"id":"b_d_03","tarea":"Temperatura de rodamientos de motor — tacto / termómetro IR"},
                {"id":"b_d_04","tarea":"Ruidos anormales en bomba o motor"},
                {"id":"b_d_05","tarea":"Vibración excesiva en cuerpo de bomba o soportes"},
                {"id":"b_d_06","tarea":"Presión de descarga — dentro de rango operativo","obs":"Registrar valor"},
                {"id":"b_d_07","tarea":"Temperatura del aceite bombeado — dentro de rango"},
                {"id":"b_d_08","tarea":"Amperaje del motor — dentro de rango nominal","obs":"Registrar valor"},
            ]
        },
        "Semanal": {
            "subtitulo": "Realizar durante la parada de fin de semana (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"b_s_01","tarea":"Revisión visual de pérdidas de aceite — cuerpo, tapas y bridas"},
                {"id":"b_s_02","tarea":"Engrase de rodamientos de motor — Bomba 1 (grasa NLGI 2)"},
                {"id":"b_s_03","tarea":"Engrase de rodamientos de motor — Bomba 2"},
                {"id":"b_s_04","tarea":"Engrase de rodamientos de motor — Bomba 3"},
                {"id":"b_s_05","tarea":"Engrase de rodamientos de motor — Bomba 4"},
                {"id":"b_s_06","tarea":"Revisión de empaquetaduras / sellos mecánicos — Bomba 1"},
                {"id":"b_s_07","tarea":"Revisión de empaquetaduras / sellos mecánicos — Bomba 2"},
                {"id":"b_s_08","tarea":"Revisión de empaquetaduras / sellos mecánicos — Bomba 3"},
                {"id":"b_s_09","tarea":"Revisión de empaquetaduras / sellos mecánicos — Bomba 4"},
                {"id":"b_s_10","tarea":"Limpieza general de cuerpos de bomba y zona de trabajo"},
                {"id":"b_s_11","tarea":"Control de apriete de bridas y conexiones"},
            ]
        },
        "Mensual": {
            "subtitulo": "Realizar durante parada programada (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"b_m_01","tarea":"Revisión completa de rodamientos de motores — juego y ruido"},
                {"id":"b_m_02","tarea":"Reemplazo de empaquetaduras si presentan pérdidas — Bomba 1"},
                {"id":"b_m_03","tarea":"Reemplazo de empaquetaduras si presentan pérdidas — Bomba 2"},
                {"id":"b_m_04","tarea":"Reemplazo de empaquetaduras si presentan pérdidas — Bomba 3"},
                {"id":"b_m_05","tarea":"Reemplazo de empaquetaduras si presentan pérdidas — Bomba 4"},
                {"id":"b_m_06","tarea":"Revisión de válvulas de alivio — apertura y cierre correctos"},
                {"id":"b_m_07","tarea":"Revisión de alineación motor / bomba — verificar acople"},
                {"id":"b_m_08","tarea":"Inspección interna si hay caída de presión o ruido"},
                {"id":"b_m_09","tarea":"Tablero eléctrico — conexiones, fusibles y protecciones"},
                {"id":"b_m_10","tarea":"Prueba de presión de descarga — registrar y comparar con histórico"},
            ]
        },
    },
    "Enfriador": {
        "Diario": {
            "subtitulo": "Completar al inicio de cada turno (cada 8 hs)",
            "columnas": ["turno_dia","turno_noche"], "col_labels": ["Turno Día","Turno Noche"],
            "tareas": [
                {"id":"en_d_01","tarea":"Inspección visual general — estado exterior y paneles de acceso"},
                {"id":"en_d_02","tarea":"Temperatura de entrada y salida del producto — registrar valores"},
                {"id":"en_d_03","tarea":"Ruidos anormales en turbina, cadenas o rodamientos"},
                {"id":"en_d_04","tarea":"Vibración excesiva en estructura o cabezal de turbina"},
                {"id":"en_d_05","tarea":"Temperatura de rodamientos — tacto / termómetro IR"},
                {"id":"en_d_06","tarea":"Nivel de aceite del reductor — visor / varilla"},
                {"id":"en_d_07","tarea":"Estado del ciclón — sin obstrucciones ni pérdidas"},
                {"id":"en_d_08","tarea":"Amperaje del motor de turbina — dentro de rango nominal","obs":"Registrar valor"},
            ]
        },
        "Quincenal": {
            "subtitulo": "Realizar cada 15 días durante parada (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"en_q_01","tarea":"Engrase de rodamientos de turbina y eje — grasa NLGI 2"},
                {"id":"en_q_02","tarea":"Engrase de cadenas de arrastre — aceite de cadena o SAE 90"},
                {"id":"en_q_03","tarea":"Limpieza de ciclón — interior y boca de descarga"},
                {"id":"en_q_04","tarea":"Limpieza de turbina — paletas y carcasa"},
                {"id":"en_q_05","tarea":"Limpieza general de cámara y zona de descarga"},
                {"id":"en_q_06","tarea":"Nivel de aceite del reductor — completar si corresponde"},
                {"id":"en_q_07","tarea":"Revisión de tensión de cadenas — ajustar si corresponde"},
                {"id":"en_q_08","tarea":"Control de apriete de tornillos de turbina y soporte de motor","obs":"Torquear según fabricante"},
            ]
        },
        "Mensual": {
            "subtitulo": "Realizar durante parada programada (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"en_m_01","tarea":"Cambio de aceite del reductor — ISO VG 220 o según fabricante","obs":"Registrar fecha y litros"},
                {"id":"en_m_02","tarea":"Revisión completa de rodamientos — juego, temperatura y ruido"},
                {"id":"en_m_03","tarea":"Inspección de cadenas — eslabones, pines y estado general"},
                {"id":"en_m_04","tarea":"Inspección de paletas de turbina — deformación y desgaste"},
                {"id":"en_m_05","tarea":"Revisión de alineación motor / reductor / eje"},
                {"id":"en_m_06","tarea":"Inspección del ciclón — fisuras, bridas y juntas"},
                {"id":"en_m_07","tarea":"Revisión de compuertas de descarga — funcionamiento y sellos"},
                {"id":"en_m_08","tarea":"Tablero eléctrico — conexiones, fusibles y protecciones"},
                {"id":"en_m_09","tarea":"Limpieza completa interior de cámara de enfriamiento"},
            ]
        },
    },
    "Prelimpieza": {
        "Diario": {
            "subtitulo": "Completar al inicio de cada turno (cada 8 hs)",
            "columnas": ["turno_dia","turno_noche"], "col_labels": ["Turno Día","Turno Noche"],
            "tareas": [
                {"id":"pl_d_01","tarea":"Inspección visual general — estado exterior de zaranda y ciclones"},
                {"id":"pl_d_02","tarea":"Ruidos anormales en vibrador, motor o estructura"},
                {"id":"pl_d_03","tarea":"Vibración correcta de zaranda — sin golpes ni movimiento irregular"},
                {"id":"pl_d_04","tarea":"Ciclones — sin obstrucciones en entrada ni boca de descarga"},
                {"id":"pl_d_05","tarea":"Pérdidas de grano visibles en juntas o tapas de zaranda"},
                {"id":"pl_d_06","tarea":"Temperatura de rodamientos del vibrador — tacto / termómetro IR"},
                {"id":"pl_d_07","tarea":"Estado de mallas — sin roturas visibles desde exterior"},
                {"id":"pl_d_08","tarea":"Amperaje del motor — dentro de rango nominal","obs":"Registrar valor"},
            ]
        },
        "Mensual": {
            "subtitulo": "Realizar durante parada programada (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"pl_m_01","tarea":"Engrase de rodamientos del vibrador — grasa NLGI 2"},
                {"id":"pl_m_02","tarea":"Revisión de correa del motor — estado y tensión"},
                {"id":"pl_m_03","tarea":"Limpieza completa de zaranda — mallas, bandejas y estructura"},
                {"id":"pl_m_04","tarea":"Inspección de mallas — roturas, obstrucciones o deformaciones","obs":"Reemplazar si están rotas"},
                {"id":"pl_m_05","tarea":"Revisión de turbina neumática — paletas y carcasa"},
                {"id":"pl_m_06","tarea":"Revisión de ciclones — limpieza interior y estado de bridas"},
                {"id":"pl_m_07","tarea":"Control de apriete de tornillos de mallas y marcos"},
                {"id":"pl_m_08","tarea":"Revisión de resortes / muelles de suspensión — estado y fatiga"},
                {"id":"pl_m_09","tarea":"Tablero eléctrico — conexiones, fusibles y protecciones"},
            ]
        },
        "Pre-Zafra": {
            "subtitulo": "Realizar antes del inicio de campaña — revisión completa con equipo detenido",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"pl_z_01","tarea":"Reemplazo de mallas si presentan roturas o deformaciones"},
                {"id":"pl_z_02","tarea":"Reengrase completo de rodamientos del vibrador"},
                {"id":"pl_z_03","tarea":"Reemplazo de correa si presenta grietas o desgaste"},
                {"id":"pl_z_04","tarea":"Inspección y reemplazo de resortes de suspensión si hay fatiga"},
                {"id":"pl_z_05","tarea":"Limpieza profunda completa — mallas, bandejas, ciclones y estructura"},
                {"id":"pl_z_06","tarea":"Revisión de turbina neumática — paletas, carcasa y conexiones de aire"},
                {"id":"pl_z_07","tarea":"Calibración de reguladores de caudal de aire"},
                {"id":"pl_z_08","tarea":"Prueba de funcionamiento en vacío — vibración, ruido y amperaje"},
                {"id":"pl_z_09","tarea":"Tablero eléctrico — conexiones, fusibles y térmicas calibradas"},
                {"id":"pl_z_10","tarea":"Estructura: soportes, pernos de anclaje y pasarelas"},
            ]
        },
    },
    "Secadora": {
        "Diario": {
            "subtitulo": "Completar al inicio de cada turno (cada 8 hs)",
            "columnas": ["turno_dia","turno_noche"], "col_labels": ["Turno Día","Turno Noche"],
            "tareas": [
                {"id":"sc_d_01","tarea":"Inspección visual general — exterior de la máquina, paneles y accesos"},
                {"id":"sc_d_02","tarea":"Temperatura de cámara de secado — registrar valor en °C"},
                {"id":"sc_d_03","tarea":"Control de llama del quemador — encendido estable, sin pulsaciones"},
                {"id":"sc_d_04","tarea":"Sensores de temperatura (termocuplas) — lectura coherente entre sí"},
                {"id":"sc_d_05","tarea":"Nivel de aceite del reductor de turbina extractora — visor / varilla"},
                {"id":"sc_d_06","tarea":"Ruidos anormales en turbinas, rodamientos o cadenas de arrastre"},
                {"id":"sc_d_07","tarea":"Vibración excesiva en estructura, conductos o soportes"},
                {"id":"sc_d_08","tarea":"Ciclones de polvo — verificar que no estén tapados ni con pérdidas"},
                {"id":"sc_d_09","tarea":"Humedad de salida del grano — muestra visual o medición con higrómetro"},
                {"id":"sc_d_10","tarea":"Presión de gas / combustible — dentro de rango operativo"},
                {"id":"sc_d_11","tarea":"Compuertas y registros de aire — posición correcta, sin trancas"},
                {"id":"sc_d_12","tarea":"Temperatura de rodamientos de turbinas — tacto / termómetro IR"},
            ]
        },
        "Semanal": {
            "subtitulo": "Realizar durante la parada de fin de semana (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"sc_s_01","tarea":"Limpieza interior de cámara de secado — eliminar depósitos de polvo y grano"},
                {"id":"sc_s_02","tarea":"Limpieza de ciclones de polvo — interior y boca de descarga"},
                {"id":"sc_s_03","tarea":"Limpieza de turbinas extractoras — paletas y carcasa"},
                {"id":"sc_s_04","tarea":"Limpieza general de estructura, bandejas y conductos de aire"},
                {"id":"sc_s_05","tarea":"Engrase de rodamientos de turbinas — grasa NLGI 2"},
                {"id":"sc_s_06","tarea":"Engrase de cadenas de arrastre — aceite de cadena o SAE 90"},
                {"id":"sc_s_07","tarea":"Revisión tensión de correas / cadenas — flecha máx. 1% entre centros"},
                {"id":"sc_s_08","tarea":"Revisión visual de paletas y bandejas internas — deformación / desgaste"},
                {"id":"sc_s_09","tarea":"Apriete de tornillos de turbinas y soportes de motor","obs":"Torquear según fabricante"},
                {"id":"sc_s_10","tarea":"Revisión de empaquetaduras y sellos de acceso — pérdidas de aire"},
                {"id":"sc_s_11","tarea":"Quemador — limpieza de boquilla y electrodos de ignición"},
                {"id":"sc_s_12","tarea":"Sensores de temperatura — cables, conectores y fijación correcta"},
                {"id":"sc_s_13","tarea":"Nivel de aceite reductor de turbina — completar si corresponde","obs":"Ver visor o varilla"},
            ]
        },
        "Mensual": {
            "subtitulo": "Realizar durante parada programada (equipo detenido — LOTO obligatorio)",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"sc_m_01","tarea":"Cambio de aceite del reductor de turbina — ISO VG 220 o según fabricante","obs":"Registrar fecha y litros"},
                {"id":"sc_m_02","tarea":"Revisión completa de rodamientos — juego, temperatura y ruido"},
                {"id":"sc_m_03","tarea":"Inspección de paletas y bandejas internas — medir desgaste con calibre","obs":"Registrar medidas"},
                {"id":"sc_m_04","tarea":"Quemador — limpieza profunda de boquilla y difusor"},
                {"id":"sc_m_05","tarea":"Calibración de termocuplas / sondas de temperatura","obs":"Contrastar con termómetro patrón"},
                {"id":"sc_m_06","tarea":"Revisión de válvulas de gas — estanqueidad y apertura correcta"},
                {"id":"sc_m_07","tarea":"Motores eléctricos — temperatura, ruido y conexiones"},
                {"id":"sc_m_08","tarea":"Alineación turbinas / motor — verificar acoples y bridas"},
                {"id":"sc_m_09","tarea":"Inspección visual estructura metálica — fisuras, corrosión, soldaduras"},
                {"id":"sc_m_10","tarea":"Compuertas de aire — funcionamiento correcto y estado de sellos"},
                {"id":"sc_m_11","tarea":"Tablero de control — alarmas activas, conexiones y fusibles"},
                {"id":"sc_m_12","tarea":"Apriete de tornillos de turbinas — torquear según especificación"},
            ]
        },
        "Pre-Zafra": {
            "subtitulo": "Realizar antes del inicio de campaña — revisión completa con equipo detenido",
            "columnas": ["fecha","ok"], "col_labels": ["Fecha","OK"], "loto": True,
            "tareas": [
                {"id":"sc_z_01","tarea":"Inspección completa de bandejas / columnas internas — reemplazar las dañadas"},
                {"id":"sc_z_02","tarea":"Cambio de aceite del reductor (si no se realizó en el último mensual)"},
                {"id":"sc_z_03","tarea":"Reengrase completo de todos los rodamientos"},
                {"id":"sc_z_04","tarea":"Cadenas — reengrase y revisión de eslabones; reemplazar si hay desgaste"},
                {"id":"sc_z_05","tarea":"Correas — ajustar tensión; reemplazar si hay grietas o desgaste lateral"},
                {"id":"sc_z_06","tarea":"Limpieza a fondo: cámara, conductos, ciclones y turbinas","obs":"Sin acumulación de polvo — riesgo incendio"},
                {"id":"sc_z_07","tarea":"Quemador completo: boquilla, electrodos, válvulas y manómetros"},
                {"id":"sc_z_08","tarea":"Calibración de todas las termocuplas y sondas de control","obs":"Registrar valores pre/post calibración"},
                {"id":"sc_z_09","tarea":"Prueba de encendido en vacío — verificar ignición y llama estable"},
                {"id":"sc_z_10","tarea":"Prueba de arranque con grano — verificar humedad de salida","obs":"Ajustar tiempo de residencia"},
                {"id":"sc_z_11","tarea":"Ciclones — sin obstrucciones, bridas y juntas ajustadas"},
                {"id":"sc_z_12","tarea":"Tablero eléctrico — conexiones, fusibles y térmicas calibradas"},
                {"id":"sc_z_13","tarea":"Prueba de alarmas de temperatura y presión"},
                {"id":"sc_z_14","tarea":"Estructura: soldaduras, soportes y fijaciones al piso"},
            ]
        },
    },
}

# ── AUTH ──────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username","").strip()
        p = request.form.get("password","")
        user = query("SELECT * FROM usuarios WHERE username=? AND activo=1", (u,), one=True)
        if user and check_password_hash(user["password_hash"], p):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["nombre"] = user["nombre"]
            return redirect(url_for("index"))
        error = "Usuario o contraseña incorrectos"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("index.html", equipos=list(CHECKLISTS.keys()))

@app.route("/formulario/<equipo>/<frecuencia>")
@login_required
def formulario(equipo, frecuencia):
    if equipo not in CHECKLISTS or frecuencia not in CHECKLISTS[equipo]:
        abort(404)
    return render_template("formulario.html", equipo=equipo, frecuencia=frecuencia,
                           cl=CHECKLISTS[equipo][frecuencia])

@app.route("/guardar", methods=["POST"])
@login_required
def guardar():
    d = request.json
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    query("INSERT INTO registros (equipo,frecuencia,turno,fecha_registro,usuario_id,datos,observaciones) VALUES (?,?,?,?,?,?,?)",
          (d["equipo"], d["frecuencia"], d.get("turno",""), fecha,
           session["user_id"], json.dumps(d.get("tareas",{})), d.get("observaciones","")),
          commit=True)
    return jsonify({"ok": True})

@app.route("/historial")
@login_required
def historial():
    equipo = request.args.get("equipo","")
    frecuencia = request.args.get("frecuencia","")
    desde = request.args.get("desde","")
    hasta = request.args.get("hasta","")
    q = "SELECT r.id,r.equipo,r.frecuencia,r.turno,r.fecha_registro,u.nombre,r.observaciones FROM registros r JOIN usuarios u ON r.usuario_id=u.id WHERE 1=1"
    params = []
    if equipo:     q += " AND r.equipo=?";       params.append(equipo)
    if frecuencia: q += " AND r.frecuencia=?";   params.append(frecuencia)
    if desde:      q += " AND r.fecha_registro>=?"; params.append(desde)
    if hasta:      q += " AND r.fecha_registro<=?"; params.append(hasta+" 23:59")
    q += " ORDER BY r.fecha_registro DESC LIMIT 300"
    rows = query(q, params)
    return render_template("historial.html", rows=rows, equipos=list(CHECKLISTS.keys()),
                           filtros={"equipo":equipo,"frecuencia":frecuencia,"desde":desde,"hasta":hasta})

@app.route("/api/frecuencias/<equipo>")
@login_required
def api_frecuencias(equipo):
    return jsonify(list(CHECKLISTS.get(equipo, {}).keys()))

@app.route("/exportar/<int:reg_id>")
@login_required
def exportar(reg_id):
    row = query("SELECT r.*,u.nombre FROM registros r JOIN usuarios u ON r.usuario_id=u.id WHERE r.id=?",
                (reg_id,), one=True)
    if not row: abort(404)
    datos = json.loads(row["datos"])
    cl = CHECKLISTS[row["equipo"]][row["frecuencia"]]
    buf = generar_pdf(row, cl, datos)
    fname = f"{row['equipo']}_{row['frecuencia']}_{row['fecha_registro'][:10]}.pdf"
    return send_file(buf, mimetype="application/pdf", download_name=fname)

# ── PDF ───────────────────────────────────────────────────────────────────────
def generar_pdf(row, cl, datos):
    C_DARK=colors.HexColor("#1C2B3A"); C_MED=colors.HexColor("#2E4A62")
    C_LIGHT=colors.HexColor("#D6E4F0"); C_ALT=colors.HexColor("#F4F8FB")
    C_BDR=colors.HexColor("#8FAFC8"); C_LOTO=colors.HexColor("#FFF3CD")
    C_LOTOB=colors.HexColor("#856404"); MARGIN=1.4*cm; CW=A4[0]-2*MARGIN
    base=getSampleStyleSheet()["Normal"]
    def ps(n,**k):
        d=dict(fontName="Helvetica",fontSize=7.5,leading=9.5,spaceAfter=0,spaceBefore=0,wordWrap="CJK"); d.update(k)
        return ParagraphStyle(n,parent=base,**d)
    ST={"task":ps("t"),"loto":ps("l",fontName="Helvetica-Bold",textColor=C_LOTOB),
        "ctr":ps("c",alignment=TA_CENTER),"hcol":ps("hc",alignment=TA_CENTER,fontName="Helvetica-Bold",textColor=colors.HexColor("#1C2B3A")),
        "hsec":ps("hs",alignment=TA_CENTER,fontName="Helvetica-Bold",textColor=colors.white,fontSize=9,leading=11),
        "tl":ps("tl",fontSize=13,leading=15,fontName="Helvetica-Bold",textColor=colors.white),
        "tc":ps("tc",fontSize=10,leading=13,fontName="Helvetica-Bold",textColor=colors.white,alignment=TA_CENTER),
        "tsub":ps("tsb",fontSize=7,leading=9,textColor=colors.HexColor("#A8C8E8"),alignment=TA_CENTER),
        "tr":ps("tr",fontSize=7.5,leading=11,textColor=colors.white,alignment=TA_RIGHT),
        "firma":ps("fi",fontSize=7.5,alignment=TA_CENTER)}
    def P(t,s="task"): return Paragraph(t,ST[s])
    buf=io.BytesIO()
    doc=SimpleDocTemplate(buf,pagesize=A4,leftMargin=MARGIN,rightMargin=MARGIN,topMargin=MARGIN,bottomMargin=MARGIN)
    story=[]
    meta=Table([[P(f"Fecha: {row['fecha_registro'][:10]}","tr")],[P(f"Turno: {row['turno'] or '—'}","tr")],[P(f"Responsable: {row['nombre']}","tr")]],colWidths=[5.0*cm],rowHeights=[0.42*cm]*3)
    meta.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),C_DARK),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),1),("BOTTOMPADDING",(0,0),(-1,-1),1)]))
    centro=Table([[P(row["equipo"],"tc")],[P(row["frecuencia"],"hsec")],[P(cl["subtitulo"],"tsub")]],colWidths=[9.0*cm],rowHeights=[0.5*cm,0.42*cm,0.35*cm])
    centro.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),C_DARK),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("TOPPADDING",(0,0),(-1,-1),1),("BOTTOMPADDING",(0,0),(-1,-1),1)]))
    hdr=Table([[P("AGRONAT S.A.","tl"),centro,meta]],colWidths=[4.2*cm,9.0*cm,5.0*cm],rowHeights=[1.45*cm])
    hdr.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),C_DARK),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("LEFTPADDING",(0,0),(0,0),10),("RIGHTPADDING",(2,0),(2,0),8),("BOX",(0,0),(-1,-1),1,C_DARK)]))
    story.append(hdr); story.append(Spacer(1,0.35*cm))
    is_d=cl["columnas"][0]=="turno_dia"
    col_w=[9.6*cm,2.2*cm,2.4*cm,4.0*cm] if is_d else [9.8*cm,2.0*cm,1.5*cm,4.9*cm]
    hdrs=["TAREA","TURNO DÍA","TURNO NOCHE","OBSERVACIONES"] if is_d else ["TAREA","FECHA","OK","OBSERVACIONES"]
    ch=Table([[P(h,"hcol") for h in hdrs]],colWidths=col_w)
    ch.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),C_LIGHT),("ALIGN",(0,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("FONTNAME",(0,0),(-1,-1),"Helvetica-Bold"),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),("BOX",(0,0),(-1,-1),0.5,C_BDR),("INNERGRID",(0,0),(-1,-1),0.3,C_BDR)]))
    story.append(ch)
    if cl.get("loto"):
        lt=Table([[P("⚠ LOTO — cortar térmica + candado personal + tarjeta EN MANTENIMIENTO","loto"),P("/ /","ctr"),P("✓","ctr"),P("Obligatorio antes de toda tarea","task")]],colWidths=col_w)
        lt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),C_LOTO),("BOX",(0,0),(-1,-1),0.5,C_BDR),("INNERGRID",(0,0),(-1,-1),0.3,C_BDR),("VALIGN",(0,0),(-1,-1),"TOP"),("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("ALIGN",(1,0),(2,0),"CENTER")]))
        story.append(lt)
    for i,t in enumerate(cl["tareas"]):
        td=datos.get(t["id"],{})
        obs_hint=t.get("obs","")
        if is_d:
            cells=[P(t["tarea"]),P("✓" if td.get("turno_dia") else "—","ctr"),P("✓" if td.get("turno_noche") else "—","ctr"),P(td.get("obs",obs_hint) or "")]
        else:
            cells=[P(t["tarea"]),P(td.get("fecha","") or "","ctr"),P("✓" if td.get("ok") else "—","ctr"),P(td.get("obs",obs_hint) or "")]
        rt=Table([cells],colWidths=col_w)
        rt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),C_ALT if i%2==0 else colors.white),("BOX",(0,0),(-1,-1),0.5,C_BDR),("INNERGRID",(0,0),(-1,-1),0.3,C_BDR),("VALIGN",(0,0),(-1,-1),"TOP"),("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("ALIGN",(1,0),(2,0),"CENTER")]))
        story.append(rt)
    story.append(Spacer(1,0.4*cm))
    oh=Table([[P("OBSERVACIONES","hsec")]],colWidths=[CW])
    oh.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),C_MED),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),("LEFTPADDING",(0,0),(-1,-1),8),("BOX",(0,0),(-1,-1),0.5,C_BDR)]))
    story.append(oh)
    ot=Table([[P(row["observaciones"] or "")]],colWidths=[CW],rowHeights=[max(1.5*cm,0.6*cm)])
    ot.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.5,C_BDR),("BACKGROUND",(0,0),(-1,-1),colors.white),("TOPPADDING",(0,0),(-1,-1),5),("LEFTPADDING",(0,0),(-1,-1),6)]))
    story.append(ot); story.append(Spacer(1,0.45*cm))
    cw2=CW/3
    ft=Table([[P(f"RESPONSABLE\n\n{row['nombre']}","firma"),P("TURNO NOCHE\n\n______________________________","firma"),P("ENCARGADO DE MANTENIMIENTO\n\n______________________________","firma")]],colWidths=[cw2]*3,rowHeights=[1.6*cm])
    ft.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.5,C_BDR),("INNERGRID",(0,0),(-1,-1),0.3,C_BDR),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ALIGN",(0,0),(-1,-1),"CENTER"),("BACKGROUND",(0,0),(-1,-1),C_ALT),("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6)]))
    story.append(ft)
    doc.build(story); buf.seek(0); return buf

# ── ADMIN ─────────────────────────────────────────────────────────────────────
@app.route("/admin/usuarios")
@login_required
def admin_usuarios():
    if session.get("username") != "admin": abort(403)
    users = query("SELECT id,username,nombre,activo FROM usuarios ORDER BY id")
    return render_template("admin_usuarios.html", users=users)

@app.route("/admin/crear_usuario", methods=["POST"])
@login_required
def crear_usuario():
    if session.get("username") != "admin": abort(403)
    u=request.form.get("username","").strip(); n=request.form.get("nombre","").strip(); p=request.form.get("password","")
    if u and n and p:
        try: query("INSERT INTO usuarios (username,password_hash,nombre) VALUES (?,?,?)",(u,generate_password_hash(p),n),commit=True)
        except: pass
    return redirect(url_for("admin_usuarios"))

@app.route("/admin/toggle_usuario/<int:uid>")
@login_required
def toggle_usuario(uid):
    if session.get("username") != "admin": abort(403)
    query("UPDATE usuarios SET activo=1-activo WHERE id=? AND username!='admin'",(uid,),commit=True)
    return redirect(url_for("admin_usuarios"))

# Initialize DB on startup (works with gunicorn)
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
