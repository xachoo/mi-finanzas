"""
Mis Finanzas — App KivyMD para gestión de finanzas personales (RD$).

Arquitectura:
  ScreenManager
  ├── LobbyScreen      — login con sesión persistente (JsonStore)
  └── MainScreen       — MDBottomNavigation con 4 pestañas:
        ├── Inicio     — Disponible Actual (ingreso − fijos pendientes)
        ├── Movimiento — Escanear voucher + formulario categorizado
        ├── Deudores   — CRUD deudores con visor de comprobante
        └── Config     — Gastos Fijos · Historial · Cerrar Sesión

Desplegable vía Buildozer en Android.
En Replit/Linux sin pantalla usa backend headless.
"""

# ── Entorno Kivy (ANTES de cualquier import de kivy) ─────────────────────────
import os, io, sys, json, base64, tempfile, urllib.request
from datetime import datetime

os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")
if not os.environ.get("DISPLAY"):
    os.environ.setdefault("KIVY_WINDOW",     "headless")
    os.environ.setdefault("KIVY_GL_BACKEND", "mock")

from kivy.config import Config as KivyConfig
KivyConfig.set("graphics", "width",  "400")
KivyConfig.set("graphics", "height", "780")

# ── Kivy / KivyMD ─────────────────────────────────────────────────────────────
from kivy.lang         import Builder
from kivy.metrics      import dp
from kivy.clock        import Clock
from kivy.utils        import platform
from kivy.properties   import StringProperty, NumericProperty, BooleanProperty
from kivy.core.window  import Window
from kivy.storage.jsonstore    import JsonStore
from kivy.uix.boxlayout       import BoxLayout
from kivy.uix.scrollview      import ScrollView
from kivy.uix.screenmanager   import ScreenManager, Screen, FadeTransition
from kivy.uix.image           import Image as KivyImage
from kivy.uix.popup           import Popup

from kivymd.app               import MDApp
from kivymd.uix.button        import MDRaisedButton, MDFlatButton, MDIconButton
from kivymd.uix.textfield     import MDTextField
from kivymd.uix.label         import MDLabel
from kivymd.uix.card          import MDCard
from kivymd.uix.list          import (MDList, OneLineListItem,
                                      TwoLineListItem, ThreeLineListItem,
                                      IconRightWidget, IconLeftWidget)
from kivymd.uix.dialog        import MDDialog
from kivymd.uix.bottomnavigation import (MDBottomNavigation,
                                          MDBottomNavigationItem)
from kivymd.uix.menu          import MDDropdownMenu
from kivymd.uix.snackbar      import Snackbar
from kivymd.uix.selectioncontrol import MDSwitch
from kivymd.uix.toolbar       import MDTopAppBar

# ── PIL ───────────────────────────────────────────────────────────────────────
try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ── OCR (pytesseract) ─────────────────────────────────────────────────────────
try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

# ── Base de datos propia ───────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db  # finanzas/db.py

# ── Constantes ────────────────────────────────────────────────────────────────
DIR        = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(DIR, "sesion.json")

TIPOS_MOVIMIENTO = [
    "Consumo",
    "Depósito",
    "Préstamo",
    "Préstamo de flotabilidad",
    "Pago de gasto fijo",
    "Pago de deudor",
]

PURPLE = [0.38, 0.30, 0.73, 1]   # #614DB5
GREEN  = [0.20, 0.73, 0.44, 1]
RED    = [0.94, 0.34, 0.34, 1]


# ══════════════════════════════════════════════════════════════════════════════
# CLASES DE WIDGETS PERSONALIZADOS
# ══════════════════════════════════════════════════════════════════════════════

class RoundCard(MDCard):
    pass

class SectionLabel(MDLabel):
    pass

class BigMonto(MDLabel):
    pass


# ══════════════════════════════════════════════════════════════════════════════
# UTILIDADES
# ══════════════════════════════════════════════════════════════════════════════

def comprimir_imagen(ruta: str, max_width: int = 800, quality: int = 55) -> bytes:
    """Comprime la imagen con PIL y retorna bytes → BLOB SQLite."""
    if not HAS_PIL:
        with open(ruta, "rb") as f:
            return f.read()
    try:
        img = PILImage.open(ruta).convert("RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception:
        with open(ruta, "rb") as f:
            return f.read()


def blob_a_temp(blob_bytes: bytes) -> str:
    """Guarda bytes de imagen en un archivo temporal; retorna la ruta."""
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.write(blob_bytes)
    tmp.close()
    return tmp.name


def ocr_desde_ruta(ruta: str) -> dict:
    """
    Extrae Descripción / Monto / Fecha / Hora de un comprobante.
    Intenta: 1) pytesseract  2) GPT-4o-mini  3) vacío (manual).
    """
    resultado = {"descripcion": "", "monto": "", "fecha": "", "hora": ""}

    if HAS_TESSERACT and HAS_PIL:
        import re
        try:
            texto  = pytesseract.image_to_string(PILImage.open(ruta), lang="spa+eng")
            lineas = [l.strip() for l in texto.splitlines() if l.strip()]
            if lineas:
                resultado["descripcion"] = lineas[0][:80]
            for linea in lineas:
                m = re.search(r"[\d]{1,3}(?:[,\.][\d]{3})*(?:[,\.][\d]{2})?", linea)
                if m and ("RD" in linea.upper() or "$" in linea):
                    resultado["monto"] = m.group().replace(",", ".")
                    break
                elif m and not resultado["monto"]:
                    resultado["monto"] = m.group().replace(",", ".")
            for linea in lineas:
                d = re.search(r"\d{2}[/\-]\d{2}[/\-]\d{2,4}", linea)
                h = re.search(r"\d{2}:\d{2}", linea)
                if d:
                    resultado["fecha"] = d.group()
                if h:
                    resultado["hora"]  = h.group()
            return resultado
        except Exception:
            pass

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key and HAS_PIL:
        import re as _re
        try:
            with open(ruta, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
            ext  = ruta.rsplit(".", 1)[-1].lower()
            mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
            body = json.dumps({
                "model": "gpt-4o-mini",
                "max_tokens": 200,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text":
                         "Eres un asistente para comprobantes bancarios dominicanos. "
                         "Extrae SOLO en JSON válido (sin markdown):\n"
                         '{"descripcion":"comercio/concepto","monto":"número sin RD$ ni comas",'
                         '"fecha":"DD/MM/YYYY o vacio","hora":"HH:MM o vacio"}'},
                        {"type": "image_url",
                         "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                    ]
                }]
            }).encode()
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=body,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            raw   = data["choices"][0]["message"]["content"]
            match = _re.search(r"\{.*?\}", raw, _re.DOTALL)
            if match:
                resultado = {**resultado, **json.loads(match.group())}
        except Exception:
            pass

    return resultado


def mostrar_snack(msg: str, ok: bool = True):
    color = GREEN if ok else RED
    s = Snackbar(text=msg, snackbar_x="8dp", snackbar_y="8dp",
                 size_hint_x=0.95)
    s.bg_color = color
    s.open()


# ══════════════════════════════════════════════════════════════════════════════
# KV LAYOUT
# ══════════════════════════════════════════════════════════════════════════════

KV = """
#:import dp kivy.metrics.dp
#:import FadeTransition kivy.uix.screenmanager.FadeTransition

<RoundCard>:
    radius: [14]
    padding: dp(16)
    elevation: 2
    md_bg_color: app.theme_cls.bg_dark

<SectionLabel>:
    font_style: 'Subtitle1'
    theme_text_color: 'Secondary'
    size_hint_y: None
    height: dp(32)

<BigMonto>:
    font_style: 'H4'
    halign: 'center'
    bold: True

ScreenManager:
    transition: FadeTransition()
    LobbyScreen:
        name: 'lobby'
    MainScreen:
        name: 'main'

# ── LOBBY ────────────────────────────────────────────────────────────────────
<LobbyScreen>:
    BoxLayout:
        orientation: 'vertical'
        padding: dp(32)
        spacing: dp(16)
        canvas.before:
            Color:
                rgba: 0.10, 0.09, 0.14, 1
            Rectangle:
                pos: self.pos
                size: self.size

        Widget:
            size_hint_y: None
            height: dp(40)

        MDLabel:
            text: '💰'
            font_size: dp(56)
            halign: 'center'
            size_hint_y: None
            height: dp(80)

        MDLabel:
            text: 'Mis Finanzas'
            font_style: 'H4'
            halign: 'center'
            bold: True
            size_hint_y: None
            height: dp(48)

        MDLabel:
            id: lbl_error
            text: ''
            halign: 'center'
            theme_text_color: 'Error'
            size_hint_y: None
            height: dp(24)

        MDTextField:
            id: tf_correo
            hint_text: 'Correo electrónico'
            icon_right: 'email-outline'
            mode: 'rectangle'
            size_hint_y: None
            height: dp(56)

        MDTextField:
            id: tf_clave
            hint_text: 'Contraseña'
            icon_right: 'lock-outline'
            password: True
            mode: 'rectangle'
            size_hint_y: None
            height: dp(56)

        Widget:
            size_hint_y: None
            height: dp(8)

        MDRaisedButton:
            text: 'INGRESAR'
            size_hint_x: 1
            size_hint_y: None
            height: dp(52)
            md_bg_color: app.theme_cls.primary_color
            on_release: root.ingresar()

        Widget:
            size_hint_y: 1

        MDLabel:
            text: 'Tu sesión se mantiene activa automáticamente.'
            halign: 'center'
            font_style: 'Caption'
            theme_text_color: 'Hint'
            size_hint_y: None
            height: dp(28)

        Widget:
            size_hint_y: None
            height: dp(20)

# ── MAIN (BottomNav) ──────────────────────────────────────────────────────────
<MainScreen>:
    MDBottomNavigation:
        id: nav
        panel_color: app.theme_cls.bg_dark

        MDBottomNavigationItem:
            name: 'inicio'
            text: 'Inicio'
            icon: 'home-outline'
            InicioTab:
                id: tab_inicio

        MDBottomNavigationItem:
            name: 'movimiento'
            text: 'Movimiento'
            icon: 'camera-outline'
            MovimientoTab:
                id: tab_movimiento

        MDBottomNavigationItem:
            name: 'deudores'
            text: 'Deudores'
            icon: 'account-group-outline'
            DeudoresTab:
                id: tab_deudores

        MDBottomNavigationItem:
            name: 'tarjetas'
            text: 'Tarjetas'
            icon: 'credit-card-outline'
            TarjetasTab:
                id: tab_tarjetas

        MDBottomNavigationItem:
            name: 'config'
            text: 'Config'
            icon: 'cog-outline'
            ConfigTab:
                id: tab_config

# ── INICIO TAB ────────────────────────────────────────────────────────────────
<InicioTab>:
    orientation: 'vertical'
    spacing: 0
    padding: 0

    MDTopAppBar:
        title: 'Mis Finanzas'
        elevation: 0
        md_bg_color: app.theme_cls.bg_dark

    ScrollView:
        BoxLayout:
            orientation: 'vertical'
            padding: dp(16)
            spacing: dp(14)
            size_hint_y: None
            height: self.minimum_height

            RoundCard:
                size_hint_y: None
                height: dp(130)
                BoxLayout:
                    orientation: 'vertical'
                    spacing: dp(4)
                    SectionLabel:
                        text: 'DISPONIBLE ACTUAL'
                        halign: 'center'
                    BigMonto:
                        id: lbl_disponible
                        text: 'RD$ --'
                        color: 0.38, 0.82, 0.55, 1
                    MDLabel:
                        id: lbl_subtitulo
                        text: 'Cargando...'
                        halign: 'center'
                        theme_text_color: 'Hint'
                        font_style: 'Caption'
                        size_hint_y: None
                        height: dp(22)

            RoundCard:
                size_hint_y: None
                height: dp(90)
                BoxLayout:
                    orientation: 'vertical'
                    spacing: dp(4)
                    SectionLabel:
                        text: 'INGRESO BASE QUINCENAL'
                    BoxLayout:
                        orientation: 'horizontal'
                        spacing: dp(8)
                        MDTextField:
                            id: tf_ingreso
                            hint_text: 'Ej: 11700'
                            input_filter: 'float'
                            mode: 'rectangle'
                        MDRaisedButton:
                            text: 'OK'
                            size_hint_x: None
                            width: dp(58)
                            on_release: root.guardar_ingreso()

            BoxLayout:
                id: box_fijos
                orientation: 'vertical'
                spacing: dp(6)
                size_hint_y: None
                height: self.minimum_height

# ── MOVIMIENTO TAB ────────────────────────────────────────────────────────────
<MovimientoTab>:
    orientation: 'vertical'
    spacing: 0

    MDTopAppBar:
        title: 'Movimiento'
        elevation: 0
        md_bg_color: app.theme_cls.bg_dark

    ScrollView:
        BoxLayout:
            orientation: 'vertical'
            padding: dp(16)
            spacing: dp(12)
            size_hint_y: None
            height: self.minimum_height

            MDRaisedButton:
                id: btn_scan
                text: '📷  Escanear Comprobante / Voucher'
                size_hint_x: 1
                size_hint_y: None
                height: dp(52)
                md_bg_color: 0.25, 0.20, 0.55, 1
                on_release: root.abrir_selector_imagen()

            MDLabel:
                id: lbl_imagen_ok
                text: ''
                halign: 'center'
                theme_text_color: 'Hint'
                font_style: 'Caption'
                size_hint_y: None
                height: dp(20)

            MDTextField:
                id: tf_descripcion
                hint_text: 'Descripción / Comercio'
                mode: 'rectangle'
                size_hint_y: None
                height: dp(52)

            MDTextField:
                id: tf_monto
                hint_text: 'Monto (RD$)'
                input_filter: 'float'
                mode: 'rectangle'
                size_hint_y: None
                height: dp(52)

            MDTextField:
                id: tf_fecha_mov
                hint_text: 'Fecha (DD/MM/YYYY)'
                mode: 'rectangle'
                size_hint_y: None
                height: dp(52)

            MDTextField:
                id: tf_hora_mov
                hint_text: 'Hora (HH:MM)'
                mode: 'rectangle'
                size_hint_y: None
                height: dp(52)

            MDRaisedButton:
                id: btn_tipo
                text: 'Tipo: Consumo  ▾'
                size_hint_x: 1
                size_hint_y: None
                height: dp(48)
                md_bg_color: app.theme_cls.bg_normal
                on_release: root.abrir_menu_tipo(self)

            BoxLayout:
                id: box_gasto_fijo
                orientation: 'vertical'
                size_hint_y: None
                height: dp(0)
                opacity: 0
                MDRaisedButton:
                    id: btn_gasto_fijo
                    text: 'Selecciona el gasto fijo  ▾'
                    size_hint_x: 1
                    size_hint_y: None
                    height: dp(48)
                    md_bg_color: app.theme_cls.bg_normal
                    on_release: root.abrir_menu_gasto_fijo(self)

            BoxLayout:
                id: box_deudor
                orientation: 'vertical'
                size_hint_y: None
                height: dp(0)
                opacity: 0
                MDRaisedButton:
                    id: btn_deudor_mov
                    text: 'Selecciona el deudor  ▾'
                    size_hint_x: 1
                    size_hint_y: None
                    height: dp(48)
                    md_bg_color: app.theme_cls.bg_normal
                    on_release: root.abrir_menu_deudor(self)

            MDRaisedButton:
                text: 'GUARDAR MOVIMIENTO'
                size_hint_x: 1
                size_hint_y: None
                height: dp(52)
                md_bg_color: app.theme_cls.primary_color
                on_release: root.guardar_movimiento()

            Widget:
                size_hint_y: None
                height: dp(20)

# ── DEUDORES TAB ──────────────────────────────────────────────────────────────
<DeudoresTab>:
    orientation: 'vertical'
    spacing: 0

    MDTopAppBar:
        title: 'Deudores'
        elevation: 0
        md_bg_color: app.theme_cls.bg_dark
        right_action_items: [['plus', lambda x: root.abrir_dialogo_deudor()]]

    ScrollView:
        MDList:
            id: lista_deudores

# ── TARJETAS TAB ─────────────────────────────────────────────────────────────
<TarjetasTab>:
    orientation: 'vertical'
    spacing: 0

    MDTopAppBar:
        title: 'Tarjetas de Crédito'
        elevation: 0
        md_bg_color: app.theme_cls.bg_dark
        right_action_items: [['plus', lambda x: root.abrir_dialogo_agregar_tarjeta()]]

    ScrollView:
        BoxLayout:
            id: box_tarjetas
            orientation: 'vertical'
            padding: dp(16)
            spacing: dp(14)
            size_hint_y: None
            height: self.minimum_height

# ── CONFIG TAB ────────────────────────────────────────────────────────────────
<ConfigTab>:
    orientation: 'vertical'
    spacing: 0

    MDTopAppBar:
        title: 'Configuración'
        elevation: 0
        md_bg_color: app.theme_cls.bg_dark

    ScrollView:
        BoxLayout:
            orientation: 'vertical'
            padding: dp(16)
            spacing: dp(16)
            size_hint_y: None
            height: self.minimum_height

            SectionLabel:
                text: 'GASTOS FIJOS DEL MES'

            MDRaisedButton:
                text: '＋ Agregar Gasto Fijo'
                size_hint_x: 1
                size_hint_y: None
                height: dp(48)
                md_bg_color: 0.25, 0.20, 0.55, 1
                on_release: root.abrir_dialogo_gasto_fijo()

            BoxLayout:
                id: box_lista_fijos
                orientation: 'vertical'
                spacing: dp(6)
                size_hint_y: None
                height: self.minimum_height

            MDLabel:
                text: ' '
                size_hint_y: None
                height: dp(8)

            SectionLabel:
                text: 'HISTORIAL DE MOVIMIENTOS'

            MDRaisedButton:
                text: '📋  Ver Historial Completo'
                size_hint_x: 1
                size_hint_y: None
                height: dp(48)
                md_bg_color: app.theme_cls.bg_normal
                on_release: root.ver_historial()

            MDLabel:
                text: ' '
                size_hint_y: None
                height: dp(8)

            SectionLabel:
                text: 'CALENDARIO DE MOVIMIENTOS'

            MDRaisedButton:
                text: '📅  Ver Calendario'
                size_hint_x: 1
                size_hint_y: None
                height: dp(48)
                md_bg_color: app.theme_cls.bg_normal
                on_release: root.ver_calendario()

            MDLabel:
                text: ' '
                size_hint_y: None
                height: dp(8)

            SectionLabel:
                text: 'CALCULADORAS FINANCIERAS'

            MDRaisedButton:
                text: '📊  Cuadro de Amortización'
                size_hint_x: 1
                size_hint_y: None
                height: dp(48)
                md_bg_color: 0.59, 0.46, 0.10, 1
                on_release: root.abrir_calculador_amort()

            MDRaisedButton:
                text: '🤝  Autofinanciamiento'
                size_hint_x: 1
                size_hint_y: None
                height: dp(48)
                md_bg_color: 0.07, 0.45, 0.25, 1
                on_release: root.abrir_autofinanciamiento()

            MDLabel:
                text: ' '
                size_hint_y: None
                height: dp(8)

            SectionLabel:
                text: 'DATOS HISTÓRICOS'

            MDRaisedButton:
                text: '📥  Importar historial legado'
                size_hint_x: 1
                size_hint_y: None
                height: dp(48)
                md_bg_color: 0.18, 0.42, 0.42, 1
                on_release: root.importar_historial_legado()

            MDLabel:
                id: lbl_migracion
                text: ''
                halign: 'center'
                theme_text_color: 'Hint'
                font_style: 'Caption'
                size_hint_y: None
                height: dp(24)

            MDLabel:
                text: ' '
                size_hint_y: None
                height: dp(8)

            SectionLabel:
                id: lbl_sesion_activa
                text: 'SESIÓN'

            MDRaisedButton:
                text: '🚪  Cerrar Sesión'
                size_hint_x: 1
                size_hint_y: None
                height: dp(52)
                md_bg_color: 0.60, 0.15, 0.15, 1
                on_release: root.cerrar_sesion()

            Widget:
                size_hint_y: None
                height: dp(80)
"""

# ══════════════════════════════════════════════════════════════════════════════
# COMPONENTES DE PANTALLAS (TABS & SCREENS)
# ══════════════════════════════════════════════════════════════════════════════

class VoucherContent(BoxLayout):
    def __init__(self, blob_bytes: bytes, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.spacing = dp(8)
        self.size_hint_y = None
        self.height = dp(340)

        ruta_tmp = blob_a_temp(blob_bytes)
        img = KivyImage(
            source=ruta_tmp,
            allow_stretch=True,
            keep_ratio=True,
        )
        self.add_widget(img)


class LobbyScreen(Screen):
    def on_enter(self):
        app = MDApp.get_running_app()
        if app.store.exists("session"):
            correo = app.store.get("session").get("correo", "")
            if correo:
                app.usuario_activo = correo
                db.reset_gastos_fijos_si_nuevo_mes(correo)
                Clock.schedule_once(lambda dt: setattr(
                    self.manager, "current", "main"), 0.1)

    def ingresar(self):
        correo = self.ids.tf_correo.text.strip().lower()
        clave  = self.ids.tf_clave.text.strip()
        lbl    = self.ids.lbl_error

        if not correo or "@" not in correo:
            lbl.text = "⚠ Ingresa un correo válido."
            return
        if len(clave) < 4:
            lbl.text = "⚠ La contraseña debe tener al menos 4 caracteres."
            return

        ok, mensaje = db.registrar_o_verificar_usuario(correo, clave)
        if not ok:
            lbl.text = f"⚠ {mensaje}"
            return

        lbl.text = ""
        app = MDApp.get_running_app()
        app.usuario_activo = correo
        app.store.put("session", correo=correo)
        db.reset_gastos_fijos_si_nuevo_mes(correo)
        self.manager.current = "main"


class MainScreen(Screen):
    def on_enter(self):
        Clock.schedule_once(self._refrescar, 0.2)

    def _refrescar(self, *_):
        self.ids.tab_inicio.refrescar()
        self.ids.tab_deudores.refrescar()
        self.ids.tab_tarjetas.refrescar()
        self.ids.tab_config.refrescar()
        self.ids.tab_movimiento.refrescar()


class InicioTab(BoxLayout):
    def on_kv_post(self, base):
        Clock.schedule_once(lambda dt: self.refrescar(), 0.5)

    def refrescar(self):
        app    = MDApp.get_running_app()
        correo = app.usuario_activo
        if not correo:
            return

        ingreso = app.store.get("ingreso_base").get("valor", 0.0) \
            if app.store.exists("ingreso_base") else 0.0

        self.ids.tf_ingreso.text = str(ingreso)

        resumen = db.calcular_disponible(correo, ingreso)
        disp    = resumen["disponible"]

        self.ids.lbl_disponible.text = f"RD$ {disp:,.2f}"
        self.ids.lbl_disponible.color = (
            [0.23, 0.78, 0.47, 1] if disp >= 0 else [0.94, 0.34, 0.34, 1]
        )

        partes_sub = [
            f"Ingresos RD$ {ingreso:,.2f}",
            f"{resumen['num_pendientes']} fijos pendientes",
        ]
        if resumen["deuda_tarjetas"] > 0:
            partes_sub.append(
                f"Tarjetas RD$ {resumen['deuda_tarjetas']:,.2f}"
            )
        self.ids.lbl_subtitulo.text = "  ·  ".join(partes_sub)

        box = self.ids.box_fijos
        box.clear_widgets()
        for f in resumen["fijos"]:
            color = [0.94, 0.34, 0.34, 1] if f["estado"] == "Pendiente" \
                    else [0.23, 0.78, 0.47, 1]
            lbl = MDLabel(
                text=f"  {'⏳' if f['estado']=='Pendiente' else '✅'}  "
                     f"{f['nombre']}    RD$ {f['monto']:,.2f}   "
                     f"[{f['estado']}]",
                theme_text_color="Custom",
                text_color=color,
                font_style="Body1",
                size_hint_y=None,
                height=dp(36),
            )
            box.add_widget(lbl)

        for t in resumen.get("tarjetas_con_deuda", []):
            lbl_t = MDLabel(
                text=(f"  💳  ···· {t['ultimos4']}"
                      f"    Próximo pago: RD$ {t['proximo_pago']:,.2f}"),
                theme_text_color="Custom",
                text_color=[1.0, 0.65, 0.0, 1],
                font_style="Body1",
                size_hint_y=None,
                height=dp(36),
            )
            box.add_widget(lbl_t)

        box.height = box.minimum_height

    def guardar_ingreso(self):
        txt = self.ids.tf_ingreso.text.strip().replace(",", ".")
        try:
            val = float(txt)
        except ValueError:
            mostrar_snack("Ingresa un monto válido.", ok=False)
            return
        MDApp.get_running_app().store.put("ingreso_base", valor=val)
        self.refrescar()
        mostrar_snack(f"Ingreso base guardado: RD$ {val:,.2f}")


class MovimientoTab(BoxLayout):
    _tipo_seleccionado = "Consumo"
    _gasto_fijo_id     = None
    _deudor_id         = None
    _imagen_bytes      = None
    _menu_tipo         = None
    _menu_gf           = None
    _menu_deu          = None

    def on_kv_post(self, base):
        self._rellenar_fecha_hora()

    def refrescar(self):
        self._rellenar_fecha_hora()
        self._tipo_seleccionado = "Consumo"
        self._gasto_fijo_id     = None
        self._deudor_id         = None
        self._imagen_bytes      = None
        self.ids.btn_tipo.text  = "Tipo: Consumo  ▾"
        self.ids.lbl_imagen_ok.text = ""
        self._toggle_extra_boxes()

    def _rellenar_fecha_hora(self):
        ahora = datetime.now()
        self.ids.tf_fecha_mov.text = ahora.strftime("%d/%m/%Y")
        self.ids.tf_hora_mov.text  = ahora.strftime("%H:%M")

    def abrir_selector_imagen(self):
        if platform == "android":
            self._abrir_camara_android()
        else:
            self._abrir_filechooser()

    def _abrir_filechooser(self):
        from kivy.uix.filechooser import FileChooserListView
        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        fc = FileChooserListView(
            filters=["*.jpg", "*.jpeg", "*.png", "*.webp"],
            path=os.path.expanduser("~"),
        )
        btn = MDRaisedButton(text="Seleccionar",
                             size_hint_y=None, height=dp(46))
        content.add_widget(fc)
        content.add_widget(btn)

        popup = Popup(title="Selecciona una imagen",
                      content=content,
                      size_hint=(0.95, 0.85))
        btn.bind(on_release=lambda *_: self._procesar_archivo(fc.selection, popup))
        popup.open()

    def _procesar_archivo(self, selection, popup):
        popup.dismiss()
        if not selection:
            return
        ruta = selection[0]
        self._procesar_imagen(ruta)

    def _abrir_camara_android(self):
        try:
            from plyer import camera
            ruta = os.path.join(tempfile.gettempdir(), "voucher_cap.jpg")
            camera.take_picture(filename=ruta,
                                on_complete=lambda path: self._procesar_imagen(path))
        except Exception:
            self._abrir_filechooser()

    def _procesar_imagen(self, ruta: str):
        if not ruta or not os.path.exists(ruta):
            return
        self._imagen_bytes = comprimir_imagen(ruta)
        size_kb = len(self._imagen_bytes) // 1024
        self.ids.lbl_imagen_ok.text = f"✅ Imagen lista ({size_kb} KB)"
        resultado = ocr_desde_ruta(ruta)
        if resultado.get("descripcion"):
            self.ids.tf_descripcion.text = resultado["descripcion"]
        if resultado.get("monto"):
            self.ids.tf_monto.text = resultado["monto"]
        if resultado.get("fecha"):
            self.ids.tf_fecha_mov.text = resultado["fecha"]
        if resultado.get("hora"):
            self.ids.tf_hora_mov.text = resultado["hora"]
        mostrar_snack("Comprobante procesado ✔")

    def abrir_menu_tipo(self, caller):
        items = [
            {"text": t,
             "viewclass": "OneLineListItem",
             "on_release": lambda x=t: self._elegir_tipo(x)}
            for t in TIPOS_MOVIMIENTO
        ]
        self._menu_tipo = MDDropdownMenu(
            caller=caller, items=items, width_mult=4)
        self._menu_tipo.open()

    def _elegir_tipo(self, tipo: str):
        if self._menu_tipo:
            self._menu_tipo.dismiss()
        self._tipo_seleccionado = tipo
        self.ids.btn_tipo.text  = f"Tipo: {tipo}  ▾"
        self._toggle_extra_boxes()

    def _toggle_extra_boxes(self):
        es_fijo   = self._tipo_seleccionado == "Pago de gasto fijo"
        es_deudor = self._tipo_seleccionado == "Pago de deudor"

        box_gf  = self.ids.box_gasto_fijo
        box_deu = self.ids.box_deudor

        box_gf.height  = dp(52) if es_fijo   else dp(0)
        box_gf.opacity = 1      if es_fijo   else 0

        box_deu.height  = dp(52) if es_deudor else dp(0)
        box_deu.opacity = 1      if es_deudor else 0

    def abrir_menu_gasto_fijo(self, caller):
        correo = MDApp.get_running_app().usuario_activo
        fijos  = [f for f in db.listar_gastos_fijos(correo)
                  if f["estado"] == "Pendiente"]
        if not fijos:
            mostrar_snack("No hay gastos fijos pendientes.", ok=False)
            return
        items = [
            {"text": f"{f['nombre']}  RD$ {f['monto']:,.2f}",
             "viewclass": "OneLineListItem",
             "on_release": lambda x=f: self._elegir_gasto_fijo(x)}
            for f in fijos
        ]
        self._menu_gf = MDDropdownMenu(caller=caller, items=items, width_mult=5)
        self._menu_gf.open()

    def _elegir_gasto_fijo(self, fijo: dict):
        if self._menu_gf:
            self._menu_gf.dismiss()
        self._gasto_fijo_id = fijo["id"]
        self.ids.btn_gasto_fijo.text = f"{fijo['nombre']}  ✔"
        if not self.ids.tf_descripcion.text:
            self.ids.tf_descripcion.text = fijo["nombre"]
        if not self.ids.tf_monto.text:
            self.ids.tf_monto.text = str(fijo["monto"])

    def abrir_menu_deudor(self, caller):
        correo   = MDApp.get_running_app().usuario_activo
        deudores = [d for d in db.listar_deudores(correo)
                    if d["estado"] == "Pendiente"]
        if not deudores:
            mostrar_snack("No hay deudores pendientes.", ok=False)
            return
        items = [
            {"text": f"{d['nombre']}  RD$ {d['monto']:,.2f}",
             "viewclass": "OneLineListItem",
             "on_release": lambda x=d: self._elegir_deudor(x)}
            for d in deudores
        ]
        self._menu_deu = MDDropdownMenu(caller=caller, items=items, width_mult=5)
        self._menu_deu.open()

    def _elegir_deudor(self, deudor: dict):
        if self._menu_deu:
            self._menu_deu.dismiss()
        self._deudor_id = deudor["id"]
        self.ids.btn_deudor_mov.text = f"{deudor['nombre']}  ✔"
        if not self.ids.tf_descripcion.text:
            self.ids.tf_descripcion.text = f"Cobro a {deudor['nombre']}"
        if not self.ids.tf_monto.text:
            self.ids.tf_monto.text = str(deudor["monto"])

    def guardar_movimiento(self):
        correo = MDApp.get_running_app().usuario_activo
        desc   = self.ids.tf_descripcion.text.strip()
        monto_txt = self.ids.tf_monto.text.strip().replace(",", ".")

        if not desc:
            mostrar_snack("Ingresa una descripción.", ok=False)
            return
        try:
            monto = float(monto_txt)
        except ValueError:
            mostrar_snack("Ingresa un monto válido.", ok=False)
            return

        db.guardar_movimiento(
            correo=correo,
            tipo=self._tipo_seleccionado,
            descripcion=desc,
            monto=monto,
            imagen_bytes=self._imagen_bytes,
            gasto_fijo_id=self._gasto_fijo_id,
            deudor_id=self._deudor_id,
        )
        mostrar_snack(f"Movimiento guardado  ✔  RD$ {monto:,.2f}")
        self.refrescar()

        main = MDApp.get_running_app().root.get_screen("main")
        main.ids.tab_inicio.refrescar()
        main.ids.tab_deudores.refrescar()
        main.ids.tab_config.refrescar()


class DeudoresTab(BoxLayout):
    _dialog = None

    def on_kv_post(self, base):
        Clock.schedule_once(lambda dt: self.refrescar(), 0.5)

    def refrescar(self):
        correo = MDApp.get_running_app().usuario_activo
        if not correo:
            return
        lista = self.ids.lista_deudores
        lista.clear_widgets()
        deudores = db.listar_deudores(correo)
        if not deudores:
            lista.add_widget(OneLineListItem(text="Sin deudores registrados."))
            return
        for d in deudores:
            pagado = d["estado"] == "Pagado"
            item = ThreeLineListItem(
                text=f"{'✅' if pagado else '⏳'}  {d['nombre']}",
                secondary_text=f"RD$ {d['monto']:,.2f}  ·  {d['estado']}",
                tertiary_text=d.get("descripcion", "")[:60],
            )
            self._agregar_acciones_deudor(item, d, pagado)
            lista.add_widget(item)

    def _agregar_acciones_deudor(self, item, deudor: dict, pagado: bool):
        box = BoxLayout(
            size_hint=(None, None),
            width=dp(80), height=dp(46),
            spacing=dp(4),
        )
        if not pagado:
            btn = MDIconButton(icon="check-circle",
                               theme_text_color="Custom",
                               text_color=GREEN)
            btn.bind(on_release=lambda *_: self._pagar_deudor(deudor["id"]))
            box.add_widget(btn)

        movs = [m for m in db.listar_movimientos(MDApp.get_running_app().usuario_activo)
                if m.get("deudor_id") == deudor["id"] and m["tiene_imagen"]]
        if movs:
            btn_foto = MDIconButton(icon="camera",
                                    theme_text_color="Custom",
                                    text_color=[0.55, 0.55, 1, 1])
            btn_foto.bind(on_release=lambda *_, mid=movs[0]["id"]:
                          self._ver_voucher(mid))
            box.add_widget(btn_foto)

        item.add_widget(box)

    def _pagar_deudor(self, id_deudor: int):
        db.actualizar_estado_deudor(id_deudor, "Pagado")
        mostrar_snack("Deudor marcado como pagado ✔")
        self.refrescar()

    def _ver_voucher(self, mov_id: int):
        blob = db.obtener_imagen_blob(mov_id)
        if not blob:
            return
        contenido = VoucherContent(blob_bytes=blob)
        dialog = MDDialog(
            title="Comprobante",
            type="custom",
            content_cls=contenido,
            buttons=[
                MDFlatButton(text="Cerrar",
                             on_release=lambda *_: dialog.dismiss()),
            ],
        )
        dialog.open()

    def abrir_dialogo_deudor(self):
        tf_nombre = MDTextField(hint_text="Nombre del deudor",
                                mode="rectangle")
        tf_desc   = MDTextField(hint_text="Descripción (opcional)",
                                mode="rectangle")
        tf_monto  = MDTextField(hint_text="Monto RD$",
                                input_filter="float", mode="rectangle")
        caja = BoxLayout(orientation="vertical", spacing=dp(8),
                         size_hint_y=None, height=dp(185))
        caja.add_widget(tf_nombre)
        caja.add_widget(tf_desc)
        caja.add_widget(tf_monto)

        dialog = None

        def guardar(*_):
            nombre = tf_nombre.text.strip()
            desc   = tf_desc.text.strip()
            monto_txt = tf_monto.text.strip().replace(",", ".")
            if not nombre:
                mostrar_snack("El nombre es requerido.", ok=False)
                return
            try:
                monto = float(monto_txt)
            except ValueError:
                mostrar_snack("Monto inválido.", ok=False)
                return
            correo = MDApp.get_running_app().usuario_activo
            db.agregar_deudor(correo, nombre, desc, monto)
            dialog.dismiss()
            self.refrescar()
            mostrar_snack(f"{nombre} agregado como deudor.")

        dialog = MDDialog(
            title="Nuevo Deudor",
            type="custom",
            content_cls=caja,
            buttons=[
                MDFlatButton(text="Cancelar",
                             on_release=lambda *_: dialog.dismiss()),
                MDRaisedButton(text="Guardar", on_release=guardar),
            ],
        )
        dialog.open()


class TarjetasTab(BoxLayout):
    def on_kv_post(self, base):
        Clock.schedule_once(lambda dt: self.refrescar(), 0.6)

    def refrescar(self):
        correo = MDApp.get_running_app().usuario_activo
        if not correo:
            return
        tarjetas = db.listar_tarjetas(correo)
        box = self.ids.box_tarjetas
        box.clear_widgets()

        if not tarjetas:
            box.add_widget(MDLabel(
                text="Sin tarjetas registradas.\nToca ＋ para agregar una.",
                halign="center",
                theme_text_color="Hint",
                size_hint_y=None,
                height=dp(80),
            ))
            return

        for t in tarjetas:
            self._agregar_card_tarjeta(box, t)

    def _agregar_card_tarjeta(self, box, tarjeta: dict):
        balance = db.calcular_balance_tarjeta(tarjeta["id"])

        card = MDCard(
            orientation="vertical",
            padding=dp(14),
            spacing=dp(6),
            size_hint_y=None,
            height=dp(200),
            radius=[14],
            elevation=3,
            md_bg_color=[0.12, 0.09, 0.28, 1],
        )

        header = BoxLayout(orientation="horizontal",
                           size_hint_y=None, height=dp(32))
        header.add_widget(MDLabel(
            text=f"💳  ···· {tarjeta['ultimos4']}",
            font_style="Subtitle1",
            bold=True,
            theme_text_color="Primary",
        ))
        btn_del = MDIconButton(
            icon="trash-can-outline",
            theme_text_color="Custom",
            text_color=RED,
            size_hint=(None, None),
            width=dp(40), height=dp(40),
        )
        btn_del.bind(
            on_release=lambda *_, tid=tarjeta["id"]: self._eliminar_tarjeta(tid))
        header.add_widget(btn_del)
        card.add_widget(header)

        card.add_widget(MDLabel(
            text=(f"Corte: día {tarjeta['dia_corte']}  ·  "
                  f"Límite pago: día {tarjeta['dia_limite_pago']}"),
            font_style="Caption",
            theme_text_color="Hint",
            size_hint_y=None,
            height=dp(20),
        ))

        montos = BoxLayout(orientation="horizontal",
                           size_hint_y=None, height=dp(68))

        col_pago = BoxLayout(orientation="vertical")
        col_pago.add_widget(MDLabel(
            text="Próximo Pago", font_style="Caption",
            theme_text_color="Hint", halign="center"))
        col_pago.add_widget(MDLabel(
            text=f"RD$ {balance['proximo_pago']:,.2f}",
            font_style="H5", bold=True, halign="center",
            theme_text_color="Custom",
            text_color=(RED if balance["proximo_pago"] > 0
                        else [0.23, 0.78, 0.47, 1]),
        ))
        montos.add_widget(col_pago)

        montos.add_widget(MDLabel(
            text="|", halign="center", theme_text_color="Hint",
            size_hint_x=None, width=dp(16)))

        col_actual = BoxLayout(orientation="vertical")
        col_actual.add_widget(MDLabel(
            text="Monto actual", font_style="Caption",
            theme_text_color="Hint", halign="center"))
        col_actual.add_widget(MDLabel(
            text=f"RD$ {balance['monto_actual']:,.2f}",
            font_style="Body1", halign="center",
            theme_text_color="Secondary",
        ))
        montos.add_widget(col_actual)
        card.add_widget(montos)

        if balance["flotabilidad"] > 0:
            card.add_widget(MDLabel(
                text=(f"⚡ Flotabilidad: RD$ {balance['flotabilidad']:,.2f}"
                      "  (no suma al próximo pago)"),
                font_style="Caption",
                theme_text_color="Custom",
                text_color=[1.0, 0.80, 0.0, 1],
                size_hint_y=None,
                height=dp(22),
            ))
        else:
            card.add_widget(MDLabel(size_hint_y=None, height=dp(4)))

        btns = BoxLayout(orientation="horizontal",
                         size_hint_y=None, height=dp(40), spacing=dp(8))
        btn_c = MDRaisedButton(
            text="＋ Consumo", md_bg_color=[0.25, 0.20, 0.55, 1], size_hint_x=1)
        btn_c.bind(on_release=lambda *_, t=tarjeta: self.abrir_dialogo_consumo(t))
        btns.add_widget(btn_c)

        btn_v = MDFlatButton(text="Ver consumos",
                             size_hint_x=None, width=dp(128))
        btn_v.bind(on_release=lambda *_, t=tarjeta: self.ver_consumos(t))
        btns.add_widget(btn_v)
        card.add_widget(btns)

        box.add_widget(card)

    def _eliminar_tarjeta(self, id_tarjeta: int):
        db.eliminar_tarjeta(id_tarjeta)
        mostrar_snack("Tarjeta eliminada.")
        self.refrescar()

    def abrir_dialogo_agregar_tarjeta(self):
        tf_4     = MDTextField(hint_text="Últimos 4 dígitos",
                               input_filter="int", mode="rectangle",
                               max_text_length=4)
        tf_corte = MDTextField(hint_text="Día de corte (1-31)",
                               input_filter="int", mode="rectangle")
        tf_lim   = MDTextField(hint_text="Día límite de pago (1-31)",
                               input_filter="int", mode="rectangle")

        caja = BoxLayout(orientation="vertical", spacing=dp(8),
                         size_hint_y=None, height=dp(185))
        caja.add_widget(tf_4)
        caja.add_widget(tf_corte)
        caja.add_widget(tf_lim)

        dialog = None

        def guardar(*_):
            ultimos4 = tf_4.text.strip()
            if len(ultimos4) != 4:
                mostrar_snack("Ingresa exactamente 4 dígitos.", ok=False)
                return
            try:
                dia_corte = int(tf_corte.text.strip())
                dia_lim   = int(tf_lim.text.strip())
                assert 1 <= dia_corte <= 31 and 1 <= dia_lim <= 31
            except Exception:
                mostrar_snack("Días deben ser entre 1 y 31.", ok=False)
                return
            correo = MDApp.get_running_app().usuario_activo
            db.agregar_tarjeta(correo, ultimos4, dia_corte, dia_lim)
            dialog.dismiss()
            self.refrescar()
            mostrar_snack(f"Tarjeta ···· {ultimos4} agregada ✔")

        dialog = MDDialog(
            title="Nueva Tarjeta de Crédito",
            type="custom", content_cls=caja,
            buttons=[
                MDFlatButton(text="Cancelar",
                             on_release=lambda *_: dialog.dismiss()),
                MDRaisedButton(text="Guardar", on_release=guardar),
            ],
        )
        dialog.open()

    def abrir_dialogo_consumo(self, tarjeta: dict):
        tf_desc  = MDTextField(hint_text="Descripción / Comercio",
                               mode="rectangle")
        tf_monto = MDTextField(hint_text="Monto (RD$)",
                               input_filter="float", mode="rectangle")
        tf_fecha = MDTextField(hint_text="Fecha (DD/MM/YYYY)", mode="rectangle",
                               text=datetime.now().strftime("%d/%m/%Y"))
        aviso = MDLabel(
            text=(f"Consumos con día > {tarjeta['dia_corte']} "
                  "se etiquetan automáticamente como flotabilidad."),
            font_style="Caption",
            theme_text_color="Hint",
            size_hint_y=None, height=dp(40),
        )
        caja = BoxLayout(orientation="vertical", spacing=dp(8),
                         size_hint_y=None, height=dp(230))
        caja.add_widget(tf_desc)
        caja.add_widget(tf_monto)
        caja.add_widget(tf_fecha)
        caja.add_widget(aviso)

        dialog = None

        def guardar(*_):
            desc  = tf_desc.text.strip()
            fecha = tf_fecha.text.strip()
            mtxt  = tf_monto.text.strip().replace(",", ".")
            if not desc:
                mostrar_snack("La descripción es requerida.", ok=False)
                return
            try:
                monto = float(mtxt)
            except ValueError:
                mostrar_snack("Monto inválido.", ok=False)
                return
            correo = MDApp.get_running_app().usuario_activo
            db.guardar_consumo_tarjeta(
                tarjeta_id=tarjeta["id"],
                correo=correo,
                descripcion=desc,
                monto=monto,
                fecha_str=fecha,
                dia_corte=tarjeta["dia_corte"],
            )
            try:
                dia = int(fecha.split("/")[0])
            except Exception:
                dia = datetime.now().day
            dialog.dismiss()
            self.refrescar()
            if dia > tarjeta["dia_corte"]:
                mostrar_snack(f"⚡ Flotabilidad guardada  RD$ {monto:,.2f}")
            else:
                mostrar_snack(f"Consumo guardado  RD$ {monto:,.2f}  ✔")

        dialog = MDDialog(
            title=f"Nuevo Consumo — ···· {tarjeta['ultimos4']}",
            type="custom", content_cls=caja,
            buttons=[
                MDFlatButton(text="Cancelar",
                             on_release=lambda *_: dialog.dismiss()),
                MDRaisedButton(text="Guardar", on_release=guardar),
            ],
        )
        dialog.open()

    def ver_consumos(self, tarjeta: dict):
        consumos = db.listar_consumos_tarjeta(tarjeta["id"])
        lista = MDList()
        if not consumos:
            lista.add_widget(OneLineListItem(text="Sin consumos registrados."))
        for c in consumos:
            flot = "  ⚡ Flotabilidad" if c["es_flotabilidad"] else ""
            lista.add_widget(TwoLineListItem(
                text=f"{'⚡ ' if c['es_flotabilidad'] else ''}  {c['descripcion'][:40]}",
                secondary_text=f"RD$ {c['monto']:,.2f}  ·  {c['fecha']}{flot}",
            ))

        sv = ScrollView(size_hint_y=None, height=dp(300))
        sv.add_widget(lista)

        d = MDDialog(
            title=f"Consumos — ···· {tarjeta['ultimos4']}",
            type="custom", content_cls=sv,
            buttons=[MDFlatButton(text="Cerrar",
                                  on_release=lambda *_: d.dismiss())],
        )
        d.open()


class CalendarioContent(BoxLayout):
    MESES_ES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

    def __init__(self, correo: str, **kwargs):
        super().__init__(**kwargs)
        self.correo    = correo
        self.orientation = "vertical"
        self.spacing   = dp(6)
        self.padding   = dp(4)
        self.size_hint_y = None
        self.height    = dp(370)
        self._anio     = datetime.now().year
        self._mes      = datetime.now().month
        self._construir()

    def _construir(self):
        import calendar
        self.clear_widgets()
        dias_movs = db.listar_dias_con_movimientos(
            self.correo, self._anio, self._mes)
        today = datetime.now()

        nav = BoxLayout(orientation="horizontal",
                        size_hint_y=None, height=dp(48))
        btn_prev = MDIconButton(icon="chevron-left",
                                size_hint_x=None, width=dp(48))
        btn_prev.bind(on_release=lambda *_: self._cambiar_mes(-1))
        nav.add_widget(btn_prev)
        nav.add_widget(MDLabel(
            text=f"{self.MESES_ES[self._mes]} {self._anio}",
            halign="center", font_style="Subtitle1", bold=True))
        btn_next = MDIconButton(icon="chevron-right",
                                size_hint_x=None, width=dp(48))
        btn_next.bind(on_release=lambda *_: self._cambiar_mes(1))
        nav.add_widget(btn_next)
        self.add_widget(nav)

        dias_hdr = BoxLayout(orientation="horizontal",
                             size_hint_y=None, height=dp(24))
        for d in ["L", "M", "X", "J", "V", "S", "D"]:
            dias_hdr.add_widget(MDLabel(
                text=d, halign="center",
                theme_text_color="Hint", font_style="Caption"))
        self.add_widget(dias_hdr)

        semanas = calendar.monthcalendar(self._anio, self._mes)
        for semana in semanas:
            fila = BoxLayout(orientation="horizontal",
                             size_hint_y=None, height=dp(46), spacing=dp(2))
            for dia in semana:
                if dia == 0:
                    fila.add_widget(BoxLayout())
                    continue

                is_today   = (dia == today.day and
                              self._mes == today.month and
                              self._anio == today.year)
                tiene_movs = dia in dias_movs

                if tiene_movs:
                    celda = BoxLayout(orientation="vertical", padding=dp(2))
                    btn = MDRaisedButton(
                        text=str(dia),
                        font_size="13sp",
                        size_hint=(1, None), height=dp(34),
                        md_bg_color=PURPLE,
                    )
                    btn.bind(on_release=lambda *_, d=dia, m=dias_movs[dia]:
                             self._ver_dia(d, m))
                    celda.add_widget(btn)
                    dot = MDLabel(
                        text="●", halign="center",
                        theme_text_color="Custom",
                        text_color=[0.23, 0.78, 0.47, 1],
                        font_style="Caption",
                        size_hint_y=None, height=dp(10),
                    )
                    celda.add_widget(dot)
                    fila.add_widget(celda)
                else:
                    color = ([1, 0.82, 0, 1] if is_today
                             else [1, 1, 1, 0.55])
                    fila.add_widget(MDLabel(
                        text=str(dia), halign="center",
                        theme_text_color="Custom",
                        text_color=color,
                        bold=is_today,
                    ))
            self.add_widget(fila)

        self.height = dp(48 + 24 + len(semanas) * 48 + 16)

    def _cambiar_mes(self, delta: int):
        mes  = self._mes + delta
        anio = self._anio
        if mes > 12:
            mes, anio = 1, anio + 1
        elif mes < 1:
            mes, anio = 12, anio - 1
        self._mes, self._anio = mes, anio
        self._construir()

    def _ver_dia(self, dia: int, movimientos: list):
        lista = MDList()
        for m in movimientos:
            lista.add_widget(TwoLineListItem(
                text=f"[{m['tipo']}]  {m['descripcion'][:38]}",
                secondary_text=f"RD$ {m['monto']:,.2f}  ·  {m['hora']}",
            ))
        sv = ScrollView(size_hint_y=None, height=dp(240))
        sv.add_widget(lista)
        d = MDDialog(
            title=f"Movimientos — {dia:02d}/{self._mes:02d}/{self._anio}",
            type="custom", content_cls=sv,
            buttons=[MDFlatButton(text="Cerrar",
                                  on_release=lambda *_: d.dismiss())],
        )
        d.open()


class ConfigTab(BoxLayout):
    _historial_dialog = None

    def on_kv_post(self, base):
        Clock.schedule_once(lambda dt: self.refrescar(), 0.5)

    def refrescar(self):
        correo = MDApp.get_running_app().usuario_activo
        if not correo:
            return
        self.ids.lbl_sesion_activa.text = f"SESIÓN: {correo}"
        self._cargar_gastos_fijos(correo)

    def _cargar_gastos_fijos(self, correo: str):
        db.reset_gastos_fijos_si_nuevo_mes(correo)
        fijos = db.listar_gastos_fijos(correo)
        box   = self.ids.box_lista_fijos
        box.clear_widgets()
        box.height = 0

        for f in fijos:
            self._agregar_item_fijo(box, f)
        box.height = box.minimum_height

    def _agregar_item_fijo(self, box: BoxLayout, fijo: dict):
        row = BoxLayout(
            orientation="horizontal",
            size_hint_y=None, height=dp(54),
            spacing=dp(8),
        )
        info = BoxLayout(orientation="vertical")
        info.add_widget(MDLabel(
            text=f"[b]{fijo['nombre']}[/b]",
            markup=True, font_style="Body1",
            theme_text_color="Primary",
        ))
        info.add_widget(MDLabel(
            text=f"RD$ {fijo['monto']:,.2f}",
            font_style="Caption",
            theme_text_color="Secondary",
        ))
        row.add_widget(info)

        sw = MDSwitch(
            active=(fijo["estado"] == "Pagado"),
            size_hint=(None, None),
            width=dp(52), height=dp(36),
        )

        def on_switch(instance, value, fid=fijo["id"]):
            nuevo = "Pagado" if value else "Pendiente"
            db.actualizar_estado_gasto_fijo(fid, nuevo)
            main = MDApp.get_running_app().root.get_screen("main")
            main.ids.tab_inicio.refrescar()

        sw.bind(active=on_switch)
        row.add_widget(sw)

        btn_del = MDIconButton(icon="trash-can-outline",
                               theme_text_color="Custom",
                               text_color=RED)
        btn_del.bind(on_release=lambda *_, fid=fijo["id"]:
                     self._eliminar_fijo(fid))
        row.add_widget(btn_del)
        box.add_widget(row)

    def abrir_dialogo_gasto_fijo(self):
        tf_nombre = MDTextField(hint_text="Nombre (ej: Luz, Internet)",
                                mode="rectangle")
        tf_monto  = MDTextField(hint_text="Monto mensual RD$",
                                input_filter="float", mode="rectangle")
        caja = BoxLayout(orientation="vertical", spacing=dp(8),
                         size_hint_y=None, height=dp(118))
        caja.add_widget(tf_nombre)
        caja.add_widget(tf_monto)

        dialog = None

        def guardar(*_):
            nombre = tf_nombre.text.strip()
            monto_txt = tf_monto.text.strip().replace(",", ".")
            if not nombre:
                mostrar_snack("El nombre es requerido.", ok=False)
                return
            try:
                monto = float(monto_txt)
            except ValueError:
                mostrar_snack("Monto inválido.", ok=False)
                return
            correo = MDApp.get_running_app().usuario_activo
            db.agregar_gasto_fijo(correo, nombre, monto)
            dialog.dismiss()
            self.refrescar()
            MDApp.get_running_app().root.get_screen("main") \
                .ids.tab_inicio.refrescar()
            mostrar_snack(f"'{nombre}' agregado como gasto fijo.")

        dialog = MDDialog(
            title="Nuevo Gasto Fijo",
            type="custom",
            content_cls=caja,
            buttons=[
                MDFlatButton(text="Cancelar",
                             on_release=lambda *_: dialog.dismiss()),
                MDRaisedButton(text="Guardar", on_release=guardar),
            ],
        )
        dialog.open()

    def _eliminar_fijo(self, id_fijo: int):
        db.eliminar_gasto_fijo(id_fijo)
        correo = MDApp.get_running_app().usuario_activo
        self._cargar_gastos_fijos(correo)
        MDApp.get_running_app().root.get_screen("main") \
            .ids.tab_inicio.refrescar()
        mostrar_snack("Gasto fijo eliminado.")

    @staticmethod
    def _calc_pmt(P: float, r: float, n: int) -> float:
        if r < 1e-12:
            return P / n
        A = (1 + r) ** n
        return P * r * A / (A - 1)

    @staticmethod
    def _calc_tabla_amort(P: float, r: float, n: int, PMT: float,
                          extras: dict) -> list:
        filas = []
        balance = P
        for i in range(1, n + 201):
            if balance < 0.01:
                break
            interes    = balance * r
            cap_base   = min(max(PMT - interes, 0.0), balance)
            extra      = min(float(extras.get(i, 0)), balance - cap_base)
            cap_total  = cap_base + extra
            cuota_real = interes + cap_total
            balance    = max(0.0, balance - cap_total)
            filas.append({
                "num": i, "cuota": cuota_real, "capital": cap_total,
                "interes": interes, "extra": extra, "balance": balance,
            })
            if balance < 0.01:
                break
        return filas

    @staticmethod
    def _resolver_campo(P_s, i_s, n_s, PMT_s, freq) -> dict:
        import math
        pp = 12 if freq == "mensual" else 24

        def blank(s):
            try:
                v = float(s.replace(",", "."))
                return v <= 0
            except Exception:
                return True

        campos_vacios = [c for c, s in [("P", P_s), ("i", i_s), ("n", n_s), ("PMT", PMT_s)] if blank(s)]
        if len(campos_vacios) != 1:
            return {"ok": False, "error":
                    "Completa exactamente 3 de los 4 campos y deja 1 vacío." if campos_vacios else
                    "Deja exactamente 1 campo vacío para que la app lo calcule."}

        def fv(s):
            return float(s.replace(",", "."))

        campo = campos_vacios[0]

        try:
            if campo == "PMT":
                P, ia, n = fv(P_s), fv(i_s), int(float(fv(n_s)))
                r = (ia / 100) / pp
                PMT = ConfigTab._calc_pmt(P, r, n)
            elif campo == "P":
                ia, n, PMT = fv(i_s), int(float(fv(n_s))), fv(PMT_s)
                r = (ia / 100) / pp
                A = (1 + r) ** n
                P = PMT * (A - 1) / (r * A) if r > 1e-12 else PMT * n
            elif campo == "n":
                P, ia, PMT = fv(P_s), fv(i_s), fv(PMT_s)
                r = (ia / 100) / pp
                if r < 1e-12:
                    n = int(math.ceil(P / PMT))
                elif PMT <= P * r:
                    return {"ok": False, "error": "Cuota insuficiente: no cubre ni los intereses."}
                else:
                    n = int(math.ceil(-math.log(1 - P * r / PMT) / math.log(1 + r)))
            else:
                P, n, PMT = fv(P_s), int(float(fv(n_s))), fv(PMT_s)
                if PMT * n <= P:
                    return {"ok": False, "error": "Cuota insuficiente para amortizar el capital."}
                r = PMT / P / n

                def f(rv):
                    if rv < 1e-12:
                        return P / n - PMT
                    A = (1 + rv) ** n
                    return P * rv * A / (A - 1) - PMT

                for _ in range(300):
                    fr = f(r)
                    h  = r * 1e-6 + 1e-12
                    fp = (f(r + h) - fr) / h
                    if abs(fp) < 1e-18:
                        break
                    r1 = r - fr / fp
                    if r1 <= 1e-10:
                        r *= 0.5
                        continue
                    if abs(r1 - r) < 1e-12:
                        r = r1
                        break
                    r = r1
                ia = r * pp * 100
        except Exception as exc:
            return {"ok": False, "error": f"Valores inválidos: {exc}"}

        r   = (fv(i_s) / 100) / pp if campo != "i" else r
        ia  = fv(i_s) if campo != "i" else ia
        PMT_fin = ConfigTab._calc_pmt(P, r, n)
        return {"ok": True, "P": P, "r": r, "n": n, "PMT": PMT_fin,
                "ia": ia, "campo": campo}

    def abrir_calculador_amort(self):
        tf_P   = MDTextField(hint_text="Capital P  (deja vacío para calcular)",
                             input_filter="float", mode="rectangle")
        tf_i   = MDTextField(hint_text="Tasa anual %  (deja vacío para calcular)",
                             input_filter="float", mode="rectangle")
        tf_n   = MDTextField(hint_text="Períodos n  (deja vacío para calcular)",
                             input_filter="int",   mode="rectangle")
        tf_pmt = MDTextField(hint_text="Cuota PMT  (deja vacío para calcular)",
                             input_filter="float", mode="rectangle")

        freq_label = MDLabel(text="Frecuencia: Mensual",
                             theme_text_color="Hint", font_style="Caption",
                             size_hint_y=None, height=dp(24))

        lbl_resultado = MDLabel(
            text="", halign="left", theme_text_color="Custom",
            text_color=[0.96, 0.75, 0.15, 1],
            font_style="Caption",
            size_hint_y=None, height=dp(56),
        )

        lista_tabla = MDList()
        sv_tabla = ScrollView(size_hint=(1, None), height=dp(220))
        sv_tabla.add_widget(lista_tabla)

        caja = BoxLayout(orientation="vertical", spacing=dp(8),
                         size_hint_y=None, height=dp(520))
        caja.add_widget(tf_P)
        caja.add_widget(tf_i)
        caja.add_widget(tf_n)
        caja.add_widget(tf_pmt)
        caja.add_widget(freq_label)
        caja.add_widget(lbl_resultado)
        caja.add_widget(sv_tabla)

        freq = ["mensual"]
        dialog = None

        def calcular(*_):
            res = ConfigTab._resolver_campo(
                tf_P.text, tf_i.text, tf_n.text, tf_pmt.text, freq[0])
            if not res["ok"]:
                lbl_resultado.text = f"⚠ {res['error']}"
                lbl_resultado.text_color = [0.94, 0.34, 0.34, 1]
                lista_tabla.clear_widgets()
                return

            P, r, n, PMT = res["P"], res["r"], res["n"], res["PMT"]
            campo = res["campo"]
            ia    = res["ia"]

            if campo == "PMT": tf_pmt.text = f"{PMT:.2f}"
            elif campo == "P": tf_P.text   = f"{P:.2f}"
            elif campo == "n": tf_n.text   = str(n)
            elif campo == "i": tf_i.text   = f"{ia:.4f}"

            filas = ConfigTab._calc_tabla_amort(P, r, n, PMT, {})
            total_pago    = sum(f["cuota"]   for f in filas)
            total_interes = sum(f["interes"] for f in filas)
            lbl_resultado.text = (
                f"✅ Cuota: RD$ {PMT:,.2f}  ·  Períodos: {n}\n"
                f"Total pagar: RD$ {total_pago:,.2f}  ·  Interés total: RD$ {total_interes:,.2f}"
            )
            lbl_resultado.text_color = [0.20, 0.85, 0.50, 1]

            lista_tabla.clear_widgets()
            for f in filas:
                lista_tabla.add_widget(TwoLineListItem(
                    text=f"#{f['num']}  Cuota: RD$ {f['cuota']:,.2f}  |  Capital: RD$ {f['capital']:,.2f}",
                    secondary_text=(
                        f"Interés: RD$ {f['interes']:,.2f}  |  "
                        f"Balance: RD$ {f['balance']:,.2f}"
                    ),
                ))

        def toggle_freq(*_):
            freq[0] = "quincenal" if freq[0] == "mensual" else "mensual"
            freq_label.text = f"Frecuencia: {freq[0].capitalize()}"

        dialog = MDDialog(
            title="📊 Calculador de Amortización",
            type="custom",
            content_cls=caja,
            buttons=[
                MDFlatButton(text="Frec.", on_release=toggle_freq),
                MDFlatButton(text="Cerrar", on_release=lambda *_: dialog.dismiss()),
                MDRaisedButton(text="⚡ Calcular", on_release=calcular),
            ],
        )
        dialog.open()

    def abrir_autofinanciamiento(self):
        tf_nombre = MDTextField(hint_text="¿Qué vas a financiar?", mode="rectangle")
        tf_precio = MDTextField(hint_text="Precio total (RD$)",
                                input_filter="float", mode="rectangle")
        tf_cuotas = MDTextField(hint_text="Número de cuotas",
                                input_filter="int", mode="rectangle")

        lbl_res = MDLabel(
            text="", halign="left", theme_text_color="Custom",
            text_color=[0.20, 0.85, 0.50, 1],
            font_style="Caption", size_hint_y=None, height=dp(40),
        )

        lista_af = MDList()
        sv_af = ScrollView(size_hint=(1, None), height=dp(180))
        sv_af.add_widget(lista_af)

        caja = BoxLayout(orientation="vertical", spacing=dp(8),
                         size_hint_y=None, height=dp(380))
        caja.add_widget(tf_nombre)
        caja.add_widget(tf_precio)
        caja.add_widget(tf_cuotas)
        caja.add_widget(lbl_res)
        caja.add_widget(sv_af)

        dialog = None

        def calcular(*_):
            try:
                precio = float(tf_precio.text.replace(",", "."))
                n      = int(tf_cuotas.text)
                if precio <= 0 or n <= 0:
                    raise ValueError
            except Exception:
                lbl_res.text = "⚠ Ingresa precio y cuotas válidos."
                lbl_res.text_color = [0.94, 0.34, 0.34, 1]
                return
            cuota = precio / n
            filas = ConfigTab._calc_tabla_amort(precio, 0.0, n, cuota, {})
            lbl_res.text = (
                f"✅ Cuota mensual: RD$ {cuota:,.2f}  ·  {n} cuotas  "
                f"·  Total: RD$ {precio:,.2f}"
            )
            lbl_res.text_color = [0.20, 0.85, 0.50, 1]
            lista_af.clear_widgets()
            for f in filas:
                lista_af.add_widget(OneLineListItem(
                    text=f"#{f['num']}  Cuota: RD$ {f['cuota']:,.2f}  |  Balance: RD$ {f['balance']:,.2f}"
                ))

        dialog = MDDialog(
            title="🤝 Autofinanciamiento (0% interés)",
            type="custom",
            content_cls=caja,
            buttons=[
                MDFlatButton(text="Cerrar", on_release=lambda *_: dialog.dismiss()),
                MDRaisedButton(text="Calcular", on_release=calcular),
            ],
        )
        dialog.open()

    def ver_historial(self):
        correo = MDApp.get_running_app().usuario_activo
        movs   = db.listar_movimientos(correo)

        lista = MDList()
        if not movs:
            lista.add_widget(OneLineListItem(text="Sin movimientos registrados."))
        for m in movs:
            item = TwoLineListItem(
                text=f"[{m['tipo']}]  {m['descripcion'][:40]}",
                secondary_text=f"RD$ {m['monto']:,.2f}  ·  {m['fecha']} {m['hora']}",
            )
            if m["tiene_imagen"]:
                btn_foto = MDIconButton(icon="camera",
                                        size_hint=(None, None),
                                        width=dp(40), height=dp(40),
                                        theme_text_color="Custom",
                                        text_color=[0.55, 0.55, 1, 1])
                btn_foto.bind(on_release=lambda *_, mid=m["id"]:
                              self._ver_voucher_historial(mid))
                item.add_widget(btn_foto)
            lista.add_widget(item)

        sv = ScrollView()
        sv.add_widget(lista)

        dialogo = MDDialog(
            title=f"Historial — {correo}",
            type="custom",
            content_cls=sv,
            buttons=[
                MDFlatButton(text="Cerrar",
                             on_release=lambda *_: dialogo.dismiss()),
            ],
        )
        dialogo.open()

    def _ver_voucher_historial(self, mov_id: int):
        blob = db.obtener_imagen_blob(mov_id)
        if not blob:
            return
        contenido = VoucherContent(blob_bytes=blob)
        d = MDDialog(
            title="Comprobante",
            type="custom",
            content_cls=contenido,
            buttons=[MDFlatButton(text="Cerrar",
                                  on_release=lambda *_: d.dismiss())],
        )
        d.open()

    def ver_calendario(self):
        correo = MDApp.get_running_app().usuario_activo
        cal_content = CalendarioContent(correo=correo)
        d = MDDialog(
            title="Calendario de Movimientos",
            type="custom",
            content_cls=cal_content,
            buttons=[
                MDFlatButton(text="Cerrar",
                             on_release=lambda *_: d.dismiss()),
            ],
        )
        d.open()

    def importar_historial_legado(self):
        import os as _os
        correo = MDApp.get_running_app().usuario_activo
        if not correo:
            mostrar_snack("Inicia sesión primero.", ok=False)
            return

        datos_path = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "datos.json"
        )
        if not _os.path.exists(datos_path):
            mostrar_snack("No se encontró datos.json.", ok=False)
            return

        dialog = None

        def _ejecutar(*_):
            dialog.dismiss()
            try:
                from migrate_json_to_db import migrar_todo
                resumen = migrar_todo(correo)
            except Exception as exc:
                mostrar_snack(f"Error en migración: {exc}", ok=False)
                return

            if "error" in resumen:
                mostrar_snack(resumen["error"], ok=False)
                return

            total = resumen.get("total", 0)
            omit  = resumen.get("omitidos", 0)
            lineas = [
                f"Gastos/movimientos: {resumen['gastos'] + resumen['extras']}",
                f"Deudores: {resumen['deudores']}",
                f"Gastos fijos: {resumen['gastos_fijos']}",
                f"Pagos fijos: {resumen['pagos_fijos']}",
                f"Pagos tarjetas: {resumen['pagos_banco_pop'] + resumen['pagos_banreservas']}",
                f"Omitidos: {omit}",
                f"Total migrados: {total}",
            ]
            resumen_txt = "\n".join(lineas)

            try:
                self.ids.lbl_migracion.text = f"✅ {total} registros importados"
            except Exception:
                pass

            d2 = MDDialog(
                title="Migración completada",
                text=resumen_txt,
                buttons=[
                    MDFlatButton(text="Cerrar",
                                 on_release=lambda *_: d2.dismiss()),
                ],
            )
            d2.open()

            try:
                main = MDApp.get_running_app().root.get_screen("main")
                main.ids.tab_inicio.refrescar()
                main.ids.tab_deudores.refrescar()
                self.refrescar()
            except Exception:
                pass

            mostrar_snack(f"✅ {total} registros importados de datos.json")

        dialog = MDDialog(
            title="Importar historial legado",
            text=(
                f"Se importarán los registros de datos.json a tu cuenta:\n"
                f"{correo}\n\n"
                "Los datos originales no serán modificados.\n"
                "¿Deseas continuar?"
            ),
            buttons=[
                MDFlatButton(text="Cancelar",
                             on_release=lambda *_: dialog.dismiss()),
                MDRaisedButton(text="Importar", on_release=_ejecutar),
            ],
        )
        dialog.open()

    def cerrar_sesion(self):
        app = MDApp.get_running_app()
        if app.store.exists("session"):
            app.store.delete("session")
        app.usuario_activo = ""
        app.root.current = "lobby"
        mostrar_snack("Sesión cerrada.")


# ══════════════════════════════════════════════════════════════════════════════
# APP PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class FinanzasApp(MDApp):
    usuario_activo = StringProperty("")

    def build(self):
        self.theme_cls.primary_palette  = "DeepPurple"
        self.theme_cls.accent_palette   = "Purple"
        self.theme_cls.theme_style      = "Dark"
        self.title = "Mis Finanzas"

        self.store = JsonStore(STORE_PATH)

        db.init_db()

        return Builder.load_string(KV)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    FinanzasApp().run()
