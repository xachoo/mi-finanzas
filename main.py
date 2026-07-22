"""
Mis Finanzas — App KivyMD para gestión de finanzas personales (RD$).

Arquitectura:
  ScreenManager
  ├── LobbyScreen      — [DESACTIVADO] login con sesión persistente
  └── MainScreen       — MDBottomNavigation con 4 pestañas:
        ├── Inicio     — Disponible Actual (ingreso − fijos pendientes)
        ├── Movimiento — Escanear voucher + formulario categorizado
        ├── Deudores   — CRUD deudores con visor de comprobante
        └── Config     — Gastos Fijos · Historial · Cerrar Sesión

Desplegable vía Buildozer en Android.
En Replit/Linux sin pantalla usa backend headless.
"""

# ── Entorno Kivy (ANTES de cualquier import de kivy) ─────────────────────────
import os, io, sys, json, base64, tempfile, urllib.request, sqlite3
from datetime import datetime

from kivy.config import Config as KivyConfig
KivyConfig.set("graphics", "width",  "400")
KivyConfig.set("graphics", "height", "740")

# ── Kivy / KivyMD ─────────────────────────────────────────────────────────────
from kivy.lang         import Builder
from kivy.metrics      import dp
from kivy.clock        import Clock
from kivy.utils        import platform
from kivy.properties   import StringProperty
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
                                      TwoLineListItem, ThreeLineListItem)
from kivymd.uix.dialog        import MDDialog
from kivymd.uix.bottomnavigation import MDBottomNavigation, MDBottomNavigationItem
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

# ── Base de datos propia (COMENTADO PARA TESTING) ─────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# import db  # finanzas/db.py

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


def inicializar_base_datos():
    # COMENTADO: Creación e inserción directa en la base de datos local
    pass
    # ruta_db = 'finanzas.db'
    # conexion = sqlite3.connect(ruta_db)
    # cursor = conexion.cursor()
    # cursor.execute('''
    #     CREATE TABLE IF NOT EXISTS usuarios (
    #         id INTEGER PRIMARY KEY AUTOINCREMENT,
    #         usuario TEXT UNIQUE NOT NULL,
    #         contrasena TEXT NOT NULL
    #     )
    # ''')
    # usuario_fijo = "test_user"
    # contrasena_fija = "123456"
    # cursor.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario_fijo,))
    # existe = cursor.fetchone()
    # if not existe:
    #     cursor.execute('''
    #         INSERT INTO usuarios (usuario, contrasena) 
    #         VALUES (?, ?)
    #     ''', (usuario_fijo, contrasena_fija))
    #     conexion.commit()
    #     print("Usuario de prueba insertado correctamente.")
    # conexion.close()


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
    # LobbyScreen:
    #     name: 'lobby'
    MainScreen:
        name: 'main'

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

        # COMENTADO: Llamada a la DB simulando datos vacíos
        # resumen = db.calcular_disponible(correo, ingreso)
        resumen = {"disponible": ingreso, "num_pendientes": 0, "deuda_tarjetas": 0, "fijos": [], "tarjetas_con_deuda": []}
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
        # COMENTADO: Carga de DB
        # correo = MDApp.get_running_app().usuario_activo
        # fijos  = [f for f in db.listar_gastos_fijos(correo) if f["estado"] == "Pendiente"]
        fijos = []
        if not fijos:
            mostrar_snack("No hay gastos fijos pendientes.", ok=False)
            return

    def _elegir_gasto_fijo(self, fijo: dict):
        if self._menu_gf:
            self._menu_gf.dismiss()
        self._gasto_fijo_id = fijo["id"]
        self.ids.btn_gasto_fijo.text = f"{fijo['nombre']}  ✔"

    def abrir_menu_deudor(self, caller):
        # COMENTADO: Carga de DB
        # correo   = MDApp.get_running_app().usuario_activo
        # deudores = [d for d in db.listar_deudores(correo) if d["estado"] == "Pendiente"]
        deudores = []
        if not deudores:
            mostrar_snack("No hay deudores pendientes.", ok=False)
            return

    def _elegir_deudor(self, deudor: dict):
        if self._menu_deu:
            self._menu_deu.dismiss()
        self._deudor_id = deudor["id"]

    def guardar_movimiento(self):
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

        # COMENTADO: Guardado en DB
        # db.guardar_movimiento(...)
        
        mostrar_snack(f"Movimiento simulado  ✔  RD$ {monto:,.2f}")
        self.refrescar()


class DeudoresTab(BoxLayout):
    def on_kv_post(self, base):
        Clock.schedule_once(lambda dt: self.refrescar(), 0.5)

    def refrescar(self):
        lista = self.ids.lista_deudores
        lista.clear_widgets()
        # COMENTADO: Carga de DB
        # deudores = db.listar_deudores(correo)
        deudores = []
        if not deudores:
            lista.add_widget(OneLineListItem(text="Sin deudores registrados (DB desactivada)."))

    def abrir_dialogo_deudor(self):
        mostrar_snack("Añadir deudor desactivado (sin base de datos).", ok=False)


class TarjetasTab(BoxLayout):
    def on_kv_post(self, base):
        Clock.schedule_once(lambda dt: self.refrescar(), 0.6)

    def refrescar(self):
        box = self.ids.box_tarjetas
        box.clear_widgets()
        box.add_widget(MDLabel(
            text="Sin tarjetas registradas (DB desactivada).",
            halign="center",
            theme_text_color="Hint",
            size_hint_y=None,
            height=dp(80),
        ))

    def abrir_dialogo_agregar_tarjeta(self):
        mostrar_snack("Función desactivada temporalmente.", ok=False)


class ConfigTab(BoxLayout):
    def on_kv_post(self, base):
        Clock.schedule_once(lambda dt: self.refrescar(), 0.5)

    def refrescar(self):
        correo = MDApp.get_running_app().usuario_activo
        if not correo:
            return
        self.ids.lbl_sesion_activa.text = f"SESIÓN: {correo}"

    def abrir_dialogo_gasto_fijo(self):
        mostrar_snack("Función desactivada temporalmente.", ok=False)

    @staticmethod
    def _calc_pmt(P: float, r: float, n: int) -> float:
        if r < 1e-12:
            return P / n
        A = (1 + r) ** n
        return P * r * A / (A - 1)

    @staticmethod
    def _calc_tabla_amort(P: float, r: float, n: int, PMT: float, extras: dict) -> list:
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
        mostrar_snack("Historial desactivado (sin base de datos).", ok=False)

    def ver_calendario(self):
        mostrar_snack("Calendario desactivado (sin base de datos).", ok=False)

    def importar_historial_legado(self):
        mostrar_snack("Importación desactivada (sin base de datos).", ok=False)

    def cerrar_sesion(self):
        app = MDApp.get_running_app()
        if app.store.exists("session"):
            app.store.delete("session")
        app.usuario_activo = "test_user"
        mostrar_snack("Sesión restablecida.")


# ══════════════════════════════════════════════════════════════════════════════
# APP PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class FinanzasApp(MDApp):
    # Usuario por defecto para iniciar directamente sin lobby
    usuario_activo = StringProperty("test_user")

    def build(self):
        # Configuración del tema visual de la aplicación (KivyMD)
        self.theme_cls.primary_palette  = "DeepPurple"
        self.theme_cls.accent_palette   = "Purple"
        self.theme_cls.theme_style      = "Dark"
        self.title = "Mis Finanzas"

        # Inicialización del almacenamiento local
        self.store = JsonStore(STORE_PATH)

        # COMENTADO: Inicialización de DB
        # db.init_db()

        # Construcción y carga de la interfaz gráfica desde la variable KV
        return Builder.load_string(KV)

    def on_start(self):
        # EVENTO AUTOMÁTICO: Fuerza el inicio en la pantalla principal ("main")
        # COMENTADO: Reset de gastos fijos desde DB
        # db.reset_gastos_fijos_si_nuevo_mes(self.usuario_activo)
        self.root.current = "main"

# Punto de entrada principal para ejecutar la aplicación
if __name__ == "__main__":
    inicializar_base_datos()
    FinanzasApp().run()