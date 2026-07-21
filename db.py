"""
db.py — Capa de acceso a datos SQLite para Mis Finanzas.

Todas las tablas incluyen correo_usuario para aislamiento estricto por cuenta.
El archivo finanzas.db se crea en el mismo directorio que este módulo.
"""

import sqlite3
import os
import hashlib
import secrets
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finanzas.db")


# ── Conexión ──────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db():
    """Crea todas las tablas si no existen. Llamar al arrancar la app."""
    with _conn() as cn:
        cn.executescript("""
            -- Credenciales de usuarios
            CREATE TABLE IF NOT EXISTS usuarios (
                correo_usuario TEXT PRIMARY KEY,
                password_hash  TEXT NOT NULL
            );

            -- Gastos recurrentes del mes (luz, internet, renta, etc.)
            CREATE TABLE IF NOT EXISTS gastos_fijos (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                correo_usuario TEXT    NOT NULL,
                nombre         TEXT    NOT NULL,
                monto          REAL    NOT NULL,
                estado         TEXT    NOT NULL DEFAULT 'Pendiente',
                mes_actual     TEXT,
                fecha_alta     TEXT    DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_gf_correo
                ON gastos_fijos(correo_usuario);

            -- Personas que deben dinero al usuario
            CREATE TABLE IF NOT EXISTS deudores (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                correo_usuario TEXT    NOT NULL,
                nombre         TEXT    NOT NULL,
                descripcion    TEXT    DEFAULT '',
                monto          REAL    NOT NULL,
                estado         TEXT    NOT NULL DEFAULT 'Pendiente',
                fecha          TEXT    DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_deu_correo
                ON deudores(correo_usuario);

            -- Cualquier movimiento de dinero; imagen_blob guardada comprimida
            CREATE TABLE IF NOT EXISTS movimientos (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                correo_usuario TEXT    NOT NULL,
                tipo           TEXT    NOT NULL,
                descripcion    TEXT    DEFAULT '',
                monto          REAL    DEFAULT 0,
                fecha          TEXT,
                hora           TEXT,
                gasto_fijo_id  INTEGER,
                deudor_id      INTEGER,
                imagen_blob    BLOB,
                created_at     TEXT    DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_mov_correo
                ON movimientos(correo_usuario);
            CREATE INDEX IF NOT EXISTS idx_mov_fecha
                ON movimientos(correo_usuario, fecha, hora);
        """)
    # Módulo de tarjetas (tablas independientes)
    init_tarjetas()


# ── Usuarios / Credenciales ───────────────────────────────────────────────────

def _hash_password(clave: str, salt: str | None = None) -> str:
    """
    Deriva una clave segura con PBKDF2-HMAC-SHA256.
    Retorna una cadena 'salt$hash' lista para almacenar en BD.
    """
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", clave.encode(), salt.encode(), 260_000)
    return f"{salt}${dk.hex()}"


def verificar_password(clave: str, stored: str) -> bool:
    """Compara la clave provista con el hash almacenado."""
    try:
        salt, _ = stored.split("$", 1)
        return _hash_password(clave, salt) == stored
    except Exception:
        return False


def registrar_o_verificar_usuario(correo: str, clave: str) -> tuple[bool, str]:
    """
    Si el correo no existe → crea usuario con la contraseña hasheada.
    Si el correo existe    → verifica la contraseña.

    Retorna (ok: bool, mensaje: str).
    """
    with _conn() as cn:
        row = cn.execute(
            "SELECT password_hash FROM usuarios WHERE correo_usuario = ?",
            (correo,)
        ).fetchone()

    if row is None:
        # Primer acceso: registrar
        ph = _hash_password(clave)
        with _conn() as cn:
            cn.execute(
                "INSERT INTO usuarios(correo_usuario, password_hash) VALUES(?,?)",
                (correo, ph)
            )
        return True, "Cuenta creada exitosamente."
    else:
        # Acceso posterior: verificar
        if verificar_password(clave, row["password_hash"]):
            return True, "Bienvenido."
        else:
            return False, "Contraseña incorrecta."


# ── Gastos Fijos ──────────────────────────────────────────────────────────────

def reset_gastos_fijos_si_nuevo_mes(correo: str):
    """
    Restablece a 'Pendiente' los gastos fijos cuyo mes_actual
    difiera del mes calendario corriente. Se llama al iniciar sesión
    y al abrir la pantalla de inicio.
    """
    mes = datetime.now().strftime("%m/%Y")
    with _conn() as cn:
        cn.execute("""
            UPDATE gastos_fijos
               SET estado = 'Pendiente',
                   mes_actual = ?
             WHERE correo_usuario = ?
               AND (mes_actual IS NULL OR mes_actual != ?)
        """, (mes, correo, mes))


def listar_gastos_fijos(correo: str) -> list:
    with _conn() as cn:
        return [dict(r) for r in cn.execute(
            "SELECT * FROM gastos_fijos WHERE correo_usuario = ? ORDER BY nombre",
            (correo,)
        )]


def agregar_gasto_fijo(correo: str, nombre: str, monto: float):
    mes = datetime.now().strftime("%m/%Y")
    with _conn() as cn:
        cn.execute(
            "INSERT INTO gastos_fijos(correo_usuario, nombre, monto, mes_actual) VALUES(?,?,?,?)",
            (correo, nombre, monto, mes)
        )


def actualizar_estado_gasto_fijo(id_fijo: int, estado: str):
    with _conn() as cn:
        cn.execute("UPDATE gastos_fijos SET estado=? WHERE id=?", (estado, id_fijo))


def eliminar_gasto_fijo(id_fijo: int):
    with _conn() as cn:
        cn.execute("DELETE FROM gastos_fijos WHERE id=?", (id_fijo,))


# ── Deudores ──────────────────────────────────────────────────────────────────

def listar_deudores(correo: str) -> list:
    with _conn() as cn:
        return [dict(r) for r in cn.execute(
            "SELECT * FROM deudores WHERE correo_usuario = ? ORDER BY fecha DESC",
            (correo,)
        )]


def agregar_deudor(correo: str, nombre: str, descripcion: str, monto: float):
    with _conn() as cn:
        cn.execute(
            "INSERT INTO deudores(correo_usuario, nombre, descripcion, monto) VALUES(?,?,?,?)",
            (correo, nombre, descripcion, monto)
        )


def actualizar_estado_deudor(id_deudor: int, estado: str):
    with _conn() as cn:
        cn.execute("UPDATE deudores SET estado=? WHERE id=?", (estado, id_deudor))


# ── Movimientos ───────────────────────────────────────────────────────────────

def guardar_movimiento(
    correo: str,
    tipo: str,
    descripcion: str,
    monto: float,
    imagen_bytes: bytes | None = None,
    gasto_fijo_id: int | None = None,
    deudor_id: int | None = None,
) -> int:
    """
    Inserta un movimiento y, según el tipo, actualiza estado de gastos fijos
    o deudores relacionados. Retorna el id del nuevo registro.
    """
    ahora = datetime.now()
    with _conn() as cn:
        cn.execute("""
            INSERT INTO movimientos
              (correo_usuario, tipo, descripcion, monto, fecha, hora,
               gasto_fijo_id, deudor_id, imagen_blob)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            correo, tipo, descripcion, monto,
            ahora.strftime("%d/%m/%Y"), ahora.strftime("%H:%M"),
            gasto_fijo_id, deudor_id, imagen_bytes
        ))
        mov_id = cn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Efectos secundarios automáticos
    if tipo == "Pago de gasto fijo" and gasto_fijo_id:
        actualizar_estado_gasto_fijo(gasto_fijo_id, "Pagado")
    if tipo == "Pago de deudor" and deudor_id:
        actualizar_estado_deudor(deudor_id, "Pagado")

    return mov_id


def listar_movimientos(correo: str) -> list:
    """Devuelve movimientos sin el BLOB (usar obtener_imagen_blob para eso)."""
    with _conn() as cn:
        return [dict(r) for r in cn.execute("""
            SELECT id, tipo, descripcion, monto, fecha, hora,
                   (imagen_blob IS NOT NULL) AS tiene_imagen
              FROM movimientos
             WHERE correo_usuario = ?
             ORDER BY created_at DESC
        """, (correo,))]


def obtener_imagen_blob(mov_id: int) -> bytes | None:
    with _conn() as cn:
        row = cn.execute(
            "SELECT imagen_blob FROM movimientos WHERE id=?", (mov_id,)
        ).fetchone()
        return bytes(row[0]) if row and row[0] else None


# ── Cálculo de disponible ─────────────────────────────────────────────────────

def calcular_disponible(correo: str, ingreso_base: float = 0.0) -> dict:
    """
    Disponible Actual = ingreso_base
                        − suma de gastos_fijos en estado 'Pendiente'
                        − suma de próximo_pago de todas las tarjetas del usuario

    Llama reset_gastos_fijos_si_nuevo_mes antes de calcular para que el
    cambio de mes se refleje sin acción manual del usuario.
    """
    reset_gastos_fijos_si_nuevo_mes(correo)
    fijos      = listar_gastos_fijos(correo)
    pendientes = [f for f in fijos if f["estado"] == "Pendiente"]
    pagados    = [f for f in fijos if f["estado"] == "Pagado"]

    total_pendiente = sum(f["monto"] for f in pendientes)
    total_pagado    = sum(f["monto"] for f in pagados)

    # Incluir deudas de tarjetas (próximo pago de cada tarjeta)
    tarjetas = listar_tarjetas(correo)
    deuda_tarjetas = 0.0
    tarjetas_con_deuda = []
    for t in tarjetas:
        bal = calcular_balance_tarjeta(t["id"])
        if bal["proximo_pago"] > 0:
            deuda_tarjetas += bal["proximo_pago"]
            tarjetas_con_deuda.append({
                **t,
                "proximo_pago": bal["proximo_pago"],
                "monto_actual": bal["monto_actual"],
            })

    disponible = ingreso_base - total_pendiente - deuda_tarjetas

    return {
        "disponible":          disponible,
        "ingreso_base":        ingreso_base,
        "total_pendiente":     total_pendiente,
        "total_pagado":        total_pagado,
        "num_pendientes":      len(pendientes),
        "num_pagados":         len(pagados),
        "fijos":               fijos,
        "deuda_tarjetas":      deuda_tarjetas,
        "tarjetas_con_deuda":  tarjetas_con_deuda,
    }


# ── Tarjetas de Crédito ───────────────────────────────────────────────────────

def init_tarjetas():
    """Crea las tablas de tarjetas si no existen (llamada desde init_db)."""
    with _conn() as cn:
        cn.executescript("""
            CREATE TABLE IF NOT EXISTS tarjetas (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                correo_usuario   TEXT    NOT NULL,
                ultimos4         TEXT    NOT NULL,
                dia_corte        INTEGER NOT NULL,
                dia_limite_pago  INTEGER NOT NULL,
                created_at       TEXT    DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_tar_correo
                ON tarjetas(correo_usuario);

            -- Consumos individuales de cada tarjeta
            CREATE TABLE IF NOT EXISTS consumos_tarjeta (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                tarjeta_id       INTEGER NOT NULL,
                correo_usuario   TEXT    NOT NULL,
                descripcion      TEXT    DEFAULT '',
                monto            REAL    NOT NULL DEFAULT 0,
                fecha            TEXT,
                es_flotabilidad  INTEGER NOT NULL DEFAULT 0,
                imagen_blob      BLOB,
                created_at       TEXT    DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_ct_tarjeta
                ON consumos_tarjeta(tarjeta_id);
        """)


def agregar_tarjeta(correo: str, ultimos4: str,
                    dia_corte: int, dia_limite_pago: int):
    with _conn() as cn:
        cn.execute(
            "INSERT INTO tarjetas(correo_usuario, ultimos4, dia_corte, dia_limite_pago)"
            " VALUES(?,?,?,?)",
            (correo, ultimos4, dia_corte, dia_limite_pago),
        )


def listar_tarjetas(correo: str) -> list:
    with _conn() as cn:
        return [dict(r) for r in cn.execute(
            "SELECT * FROM tarjetas WHERE correo_usuario = ? ORDER BY created_at DESC",
            (correo,),
        )]


def eliminar_tarjeta(id_tarjeta: int):
    with _conn() as cn:
        cn.execute("DELETE FROM consumos_tarjeta WHERE tarjeta_id=?", (id_tarjeta,))
        cn.execute("DELETE FROM tarjetas WHERE id=?", (id_tarjeta,))


def guardar_consumo_tarjeta(
    tarjeta_id: int,
    correo: str,
    descripcion: str,
    monto: float,
    fecha_str: str,
    dia_corte: int,
    imagen_bytes: bytes | None = None,
) -> int:
    """
    Guarda un consumo. Detecta flotabilidad comparando el día del gasto
    contra dia_corte: si día > dia_corte → es flotabilidad (no se suma
    al Próximo Pago, solo al Monto Actual).
    """
    try:
        dia = int(fecha_str.split("/")[0])
    except Exception:
        dia = datetime.now().day

    es_flotabilidad = 1 if dia > dia_corte else 0

    with _conn() as cn:
        cn.execute("""
            INSERT INTO consumos_tarjeta
              (tarjeta_id, correo_usuario, descripcion, monto,
               fecha, es_flotabilidad, imagen_blob)
            VALUES (?,?,?,?,?,?,?)
        """, (tarjeta_id, correo, descripcion, monto,
              fecha_str, es_flotabilidad, imagen_bytes))
        return cn.execute("SELECT last_insert_rowid()").fetchone()[0]


def listar_consumos_tarjeta(tarjeta_id: int) -> list:
    with _conn() as cn:
        return [dict(r) for r in cn.execute("""
            SELECT id, descripcion, monto, fecha, es_flotabilidad,
                   (imagen_blob IS NOT NULL) AS tiene_imagen
              FROM consumos_tarjeta
             WHERE tarjeta_id = ?
             ORDER BY fecha DESC, id DESC
        """, (tarjeta_id,))]


def calcular_balance_tarjeta(tarjeta_id: int) -> dict:
    """
    Retorna:
      proximo_pago  = suma de consumos NO flotabilidad
      monto_actual  = suma total de todos los consumos
      flotabilidad  = suma de consumos flotabilidad
    """
    consumos = listar_consumos_tarjeta(tarjeta_id)
    monto_actual = sum(c["monto"] for c in consumos)
    proximo_pago = sum(c["monto"] for c in consumos if not c["es_flotabilidad"])
    flotabilidad = sum(c["monto"] for c in consumos if c["es_flotabilidad"])
    return {
        "monto_actual":  monto_actual,
        "proximo_pago":  proximo_pago,
        "flotabilidad":  flotabilidad,
        "num_consumos":  len(consumos),
    }


# ── Calendario ────────────────────────────────────────────────────────────────

def listar_dias_con_movimientos(correo: str, anio: int, mes: int) -> dict:
    """
    Devuelve {día_int: [lista de movimientos]} para el mes/año indicado.
    Solo movimientos de la tabla principal (no consumos de tarjeta).
    """
    with _conn() as cn:
        rows = cn.execute("""
            SELECT id, tipo, descripcion, monto, fecha, hora
              FROM movimientos
             WHERE correo_usuario = ?
             ORDER BY hora
        """, (correo,)).fetchall()

    resultado: dict = {}
    for row in rows:
        row = dict(row)
        fecha = row.get("fecha", "") or ""
        try:
            partes = fecha.split("/")
            dia = int(partes[0])
            m   = int(partes[1])
            a   = int(partes[2])
        except Exception:
            continue
        if m == mes and a == anio:
            resultado.setdefault(dia, []).append(row)
    return resultado
