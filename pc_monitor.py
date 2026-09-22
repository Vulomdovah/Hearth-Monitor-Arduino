# pc_monitor.py
# Monitor cardiaco para usar como overlay sobre un juego.
#
# Flujo:
#   1) Pantalla de conexion: instrucciones de cableado + conectar al Arduino.
#      No deja avanzar hasta detectar un latido real (evita seguir con
#      electrodos mal puestos).
#   2) Pantalla de configuracion: umbrales, tamano, fluidez de la linea.
#      Cada campo tiene un icono (i) con tooltip explicando que hace.
#   3) Boton "Jugar": abre el overlay transparente encima del juego.
#      Cerrar la ventana de configuracion cierra el overlay con ella.
#
# Instalar dependencia:
#   pip install pyserial
#
# Para convertirlo en .exe (hacerlo en tu PC con Windows, parado en la
# carpeta donde esta este archivo):
#   pip install pyinstaller
#   pyinstaller --onefile --windowed --name MonitorCardiaco pc_monitor.py
#   El .exe queda en la carpeta dist/

import serial
import tkinter as tk
from collections import deque

# ---------------------------------------------------------------------------
# VALORES POR DEFECTO (editables desde la pantalla de configuracion)
# ---------------------------------------------------------------------------

DEFAULTS = {
    "port": "COM4",
    "baud": "115200",
    "umbral_bajo_urgencia": "40",
    "umbral_bajo": "60",
    "umbral_alto": "120",
    "umbral_alto_urgencia": "150",
    "raw_min": "300",
    "raw_max": "700",
    "ancho": "420",
    "alto": "240",
    "wave_points": "400",
}

TOOLTIPS = {
    "port": "Puerto COM donde esta conectado tu Arduino.\n"
            "Se ve en el IDE de Arduino: Herramientas > Puerto.",
    "baud": "Velocidad de comunicacion serie.\n"
            "Debe ser igual al Serial.begin() del sketch (115200).",
    "umbral_bajo_urgencia": "Por debajo de este BPM se marca como\n"
                             "emergencia de pulso bajo (azul + sonido).",
    "umbral_bajo": "Por debajo de este BPM se considera\n"
                    "pulso bajo (color azul).",
    "umbral_alto": "Por encima de este BPM se considera\n"
                    "pulso alto (color rojo).",
    "umbral_alto_urgencia": "Por encima de este BPM se marca como\n"
                              "emergencia de pulso alto (rojo + sonido).",
    "raw_min": "Valor minimo esperado de la senal cruda (0-1023).\n"
               "Mira los valores 'R' en el Monitor Serie de Arduino\n"
               "para calibrarlo bien.",
    "raw_max": "Valor maximo esperado de la senal cruda (0-1023).\n"
               "Mira los valores 'R' en el Monitor Serie de Arduino\n"
               "para calibrarlo bien.",
    "ancho": "Ancho en pixeles de la ventana overlay\n"
             "que se muestra encima del juego.",
    "alto": "Alto en pixeles de la ventana overlay\n"
            "que se muestra encima del juego.",
    "wave_points": "Cuantas muestras se ven en la linea a la vez.\n"
                    "Mas puntos = linea mas fluida y suave, pero\n"
                    "reacciona mas lento a cambios repentinos.\n"
                    "Menos puntos = mas nerviosa pero mas inmediata.",
}

SUAVIZADO = 0.30          # 0 = sin suavizar, 1 = muy suave
MARGEN_VERTICAL = 20
REFRESH_MS = 33           # dibuja a ~30 fps

COLOR_LINEA = "lime"      # la linea del ECG SIEMPRE es este color, fijo
COLOR_NORMAL = "lime"
COLOR_ALTO = "red"
COLOR_BAJO = "deepskyblue"
COLOR_NEUTRO = "white"
TRANSPARENT_KEY = "black"

BG = "#111111"


class Tooltip:
    """Globito de ayuda que aparece al pasar el mouse sobre un widget."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + 20
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        self.tip.geometry(f"+{x}+{y}")
        label = tk.Label(self.tip, text=self.text, justify="left",
                          bg="#ffffe0", fg="black", font=("Consolas", 9),
                          relief="solid", borderwidth=1, padx=6, pady=4)
        label.pack()

    def hide(self, event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class MonitorApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Monitor Cardiaco")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.entries = {}
        self.ser = None
        self.overlay = None
        self.canvas = None
        self.bpm_text_id = None
        self.status_text_id = None

        self.wave_data = deque(maxlen=400)
        self.smoothed_val = 0.0
        self.history = deque(maxlen=10)

        self.cfg = dict(DEFAULTS)
        self.pulso_detectado = False

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.build_connection_screen()
        self.root.mainloop()

    def on_close(self):
        self.close_overlay()
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.root.destroy()

    # ------------------------------------------------------------------
    # PANTALLA 1: CONEXION (instrucciones + no avanza sin pulso detectado)
    # ------------------------------------------------------------------
    def build_connection_screen(self):
        self.conn_frame = tk.Frame(self.root, bg=BG, padx=20, pady=20)
        self.conn_frame.pack()

        tk.Label(self.conn_frame, text="Conectar el Arduino",
                 font=("Consolas", 14, "bold"), fg="white", bg=BG
                 ).pack(pady=(0, 10))

        instrucciones = (
            "Cableado del AD8232 a la placa UNO:\n\n"
            "  OUTPUT -> A0\n"
            "  LO+    -> D10\n"
            "  LO-    -> D11\n"
            "  3.3V   -> 3.3V\n"
            "  GND    -> GND\n\n"
            "Peg\u00e1 los 3 electrodos en la piel (limpia y seca)\n"
            "y conect\u00e1 el cable de 3 puntas al sensor.\n"
            "Confirm\u00e1 que el sketch heart_monitor.ino ya\n"
            "est\u00e9 subido a la placa antes de continuar."
        )
        tk.Label(self.conn_frame, text=instrucciones, justify="left",
                 font=("Consolas", 10), fg="#cccccc", bg=BG
                 ).pack(pady=(0, 15))

        form = tk.Frame(self.conn_frame, bg=BG)
        form.pack()

        tk.Label(form, text="Puerto COM", font=("Consolas", 10),
                 fg="white", bg=BG).grid(row=0, column=0, sticky="w")
        self.conn_port_var = tk.StringVar(value=self.cfg["port"])
        tk.Entry(form, textvariable=self.conn_port_var, width=10,
                  font=("Consolas", 10)).grid(row=0, column=1, padx=(10, 0))

        tk.Label(form, text="Baudios", font=("Consolas", 10),
                 fg="white", bg=BG).grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.conn_baud_var = tk.StringVar(value=self.cfg["baud"])
        tk.Entry(form, textvariable=self.conn_baud_var, width=10,
                  font=("Consolas", 10)).grid(row=1, column=1, padx=(10, 0), pady=(5, 0))

        self.conn_connect_btn = tk.Button(self.conn_frame, text="Conectar",
                                           font=("Consolas", 11, "bold"),
                                           command=self.try_connect)
        self.conn_connect_btn.pack(pady=(15, 5))

        self.conn_status = tk.Label(self.conn_frame, text="Todavia no conectado",
                                     font=("Consolas", 10), fg="gray", bg=BG)
        self.conn_status.pack()

        self.conn_continue_btn = tk.Button(
            self.conn_frame, text="Continuar a configuracion",
            font=("Consolas", 11, "bold"), state="disabled",
            command=self.go_to_config)
        self.conn_continue_btn.pack(pady=(15, 0))

    def try_connect(self):
        port = self.conn_port_var.get()
        try:
            baud = int(self.conn_baud_var.get())
        except ValueError:
            self.conn_status.config(text="Baudios invalidos", fg="red")
            return

        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass

        try:
            self.ser = serial.Serial(port, baud, timeout=0.1)
            self.cfg["port"] = port
            self.cfg["baud"] = str(baud)
            self.pulso_detectado = False
            self.conn_status.config(text="Conectado. Esperando pulso...",
                                     fg="white")
            self.poll_connection()
        except Exception as e:
            self.conn_status.config(text=f"Error: {e}", fg="red")

    def poll_connection(self):
        """Escucha el puerto durante la pantalla de conexion. No deja
        continuar hasta recibir al menos un BPM real (tag 'B')."""
        if not self.ser or self.pulso_detectado:
            return

        if self.ser.in_waiting:
            try:
                raw = self.ser.read(self.ser.in_waiting).decode(errors="ignore")
                lines = raw.splitlines()
            except Exception:
                lines = []

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                tag = line[0]

                if tag == "L":
                    self.conn_status.config(fg="yellow",
                                             text="Electrodos desconectados, ajustalos")

                elif tag == "B":
                    self.pulso_detectado = True
                    self.conn_status.config(
                        text="Pulso detectado! Ya podes continuar",
                        fg=COLOR_NORMAL)
                    self.conn_continue_btn.config(state="normal")
                    return  # dejar de sondear, ya cumplio el objetivo

        self.root.after(50, self.poll_connection)

    def go_to_config(self):
        self.conn_frame.destroy()
        self.build_config_screen()

    # ------------------------------------------------------------------
    # PANTALLA 2: CONFIGURACION
    # ------------------------------------------------------------------
    def build_config_screen(self):
        self.config_frame = tk.Frame(self.root, bg=BG, padx=20, pady=20)
        self.config_frame.pack()

        tk.Label(self.config_frame, text="Configuracion del monitor",
                 font=("Consolas", 14, "bold"), fg="white", bg=BG
                 ).grid(row=0, column=0, columnspan=3, pady=(0, 15))

        campos = [
            ("port", "Puerto COM"),
            ("baud", "Baudios"),
            ("umbral_bajo_urgencia", "Umbral bajo-urgencia (bpm)"),
            ("umbral_bajo", "Umbral bajo (bpm)"),
            ("umbral_alto", "Umbral alto (bpm)"),
            ("umbral_alto_urgencia", "Umbral alto-urgencia (bpm)"),
            ("raw_min", "Senal cruda minima"),
            ("raw_max", "Senal cruda maxima"),
            ("ancho", "Ancho overlay (px)"),
            ("alto", "Alto overlay (px)"),
            ("wave_points", "Fluidez de la linea (puntos)"),
        ]

        for i, (key, label) in enumerate(campos, start=1):
            tk.Label(self.config_frame, text=label, font=("Consolas", 10),
                     fg="white", bg=BG, anchor="w").grid(
                row=i, column=0, sticky="w", pady=3)

            var = tk.StringVar(value=self.cfg[key])
            entry = tk.Entry(self.config_frame, textvariable=var, width=12,
                              font=("Consolas", 10))
            entry.grid(row=i, column=1, pady=3, padx=(10, 5))
            self.entries[key] = var

            info = tk.Label(self.config_frame, text="\u24d8", font=("Consolas", 11, "bold"),
                             fg="#66aaff", bg=BG, cursor="question_arrow")
            info.grid(row=i, column=2, sticky="w")
            Tooltip(info, TOOLTIPS.get(key, ""))

        self.error_label = tk.Label(self.config_frame, text="", fg="red",
                                     bg=BG, font=("Consolas", 9))
        self.error_label.grid(row=len(campos) + 1, column=0, columnspan=3)

        play_btn = tk.Button(self.config_frame, text="Jugar", width=20,
                              font=("Consolas", 12, "bold"), bg="lime",
                              command=self.start_overlay)
        play_btn.grid(row=len(campos) + 2, column=0, columnspan=3, pady=(15, 0))

        hint = tk.Label(self.config_frame,
                         text="Si cierras esta ventana, el overlay se cierra con ella",
                         fg="gray", bg=BG, font=("Consolas", 8))
        hint.grid(row=len(campos) + 3, column=0, columnspan=3, pady=(8, 0))

    def read_config_from_form(self):
        try:
            for key, var in self.entries.items():
                self.cfg[key] = var.get()

            self.cfg_port = self.cfg["port"]
            self.cfg_baud = int(self.cfg["baud"])
            self.cfg_umbral_bajo_urg = int(self.cfg["umbral_bajo_urgencia"])
            self.cfg_umbral_bajo = int(self.cfg["umbral_bajo"])
            self.cfg_umbral_alto = int(self.cfg["umbral_alto"])
            self.cfg_umbral_alto_urg = int(self.cfg["umbral_alto_urgencia"])
            self.cfg_raw_min = int(self.cfg["raw_min"])
            self.cfg_raw_max = int(self.cfg["raw_max"])
            self.cfg_ancho = int(self.cfg["ancho"])
            self.cfg_alto = int(self.cfg["alto"])
            self.cfg_wave_points = max(20, int(self.cfg["wave_points"]))
            return True
        except ValueError:
            self.error_label.config(text="Revisa que todos los campos sean numeros validos")
            return False

    # ------------------------------------------------------------------
    # PANTALLA 3: OVERLAY (modo juego) - ventana hija de la config.
    # ------------------------------------------------------------------
    def start_overlay(self):
        if not self.read_config_from_form():
            return

        self.close_overlay()

        # si el puerto/baudios cambiaron en config, reconectar
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        try:
            self.ser = serial.Serial(self.cfg_port, self.cfg_baud, timeout=0.1)
        except Exception as e:
            self.error_label.config(text=f"No se pudo conectar: {e}")
            return

        self.overlay = tk.Toplevel(self.root)
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        try:
            self.overlay.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            pass

        self.overlay.geometry(f"{self.cfg_ancho}x{self.cfg_alto}+40+40")
        self.overlay.configure(bg=TRANSPARENT_KEY)
        self.overlay.protocol("WM_DELETE_WINDOW", self.close_overlay)

        self.canvas = tk.Canvas(self.overlay, width=self.cfg_ancho, height=self.cfg_alto,
                                 bg=TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack()

        self.bpm_text_id = self.canvas.create_text(
            self.cfg_ancho - 15, 15, anchor="ne", text="--",
            font=("Consolas", 32, "bold"), fill=COLOR_NEUTRO)
        self.status_text_id = self.canvas.create_text(
            15, 15, anchor="nw", text="Conectado",
            font=("Consolas", 12, "bold"), fill=COLOR_NEUTRO)

        self.wave_data = deque([0] * self.cfg_wave_points, maxlen=self.cfg_wave_points)
        self.smoothed_val = self.cfg_raw_min + (self.cfg_raw_max - self.cfg_raw_min) / 2

        self.overlay.bind("<Escape>", lambda e: self.close_overlay())

        self.read_serial()
        self.render_loop()

    def close_overlay(self, event=None):
        if self.overlay:
            self.overlay.destroy()
            self.overlay = None
            self.canvas = None

    # ------------------------------------------------------------------
    # LOGICA DE SENSOR / DIBUJO
    # ------------------------------------------------------------------
    def zona_de(self, bpm):
        if bpm >= self.cfg_umbral_alto_urg:
            return COLOR_ALTO, f"Pulso muy alto: {bpm} bpm", True
        if bpm >= self.cfg_umbral_alto:
            return COLOR_ALTO, f"Pulso alto: {bpm} bpm", False
        if bpm <= self.cfg_umbral_bajo_urg:
            return COLOR_BAJO, f"Pulso muy bajo: {bpm} bpm", True
        if bpm <= self.cfg_umbral_bajo:
            return COLOR_BAJO, f"Pulso bajo: {bpm} bpm", False
        return COLOR_NORMAL, "Normal", False

    def push_sample(self, raw_val):
        self.smoothed_val += (raw_val - self.smoothed_val) * (1 - SUAVIZADO)
        self.wave_data.append(self.smoothed_val)

    def draw_wave(self):
        if not self.canvas:
            return
        self.canvas.delete("wave")

        mid_y = self.cfg_alto / 2
        half_range = (self.cfg_raw_max - self.cfg_raw_min) / 2 or 1
        center_raw = self.cfg_raw_min + half_range
        scale = (self.cfg_alto / 2) - MARGEN_VERTICAL
        y_min = MARGEN_VERTICAL
        y_max = self.cfg_alto - MARGEN_VERTICAL
        step = self.cfg_ancho / (self.cfg_wave_points - 1)

        flat = []
        for i, val in enumerate(self.wave_data):
            x = i * step
            norm = (val - center_raw) / half_range
            norm = max(-1.0, min(1.0, norm))
            y = mid_y - (norm * scale)
            y = max(y_min, min(y_max, y))
            flat.extend((x, y))

        if len(flat) >= 4:
            self.canvas.create_line(*flat, fill=COLOR_LINEA, width=2, tags="wave")

    def render_loop(self):
        if not self.overlay:
            return
        self.draw_wave()
        self.overlay.after(REFRESH_MS, self.render_loop)

    def read_serial(self):
        if not self.canvas or not self.overlay:
            return

        if self.ser and self.ser.in_waiting:
            try:
                raw = self.ser.read(self.ser.in_waiting).decode(errors="ignore")
                lines = raw.splitlines()
            except Exception:
                lines = []

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                tag = line[0]
                payload = line[1:]

                if tag == "R":
                    try:
                        self.push_sample(int(payload))
                    except ValueError:
                        pass

                elif tag == "B":
                    try:
                        bpm = int(payload)
                    except ValueError:
                        continue

                    self.history.append(bpm)
                    color, msg, urgente = self.zona_de(bpm)

                    self.canvas.itemconfig(self.bpm_text_id, text=str(bpm), fill=color)
                    self.canvas.itemconfig(self.status_text_id, text=msg, fill=color)

                    if urgente:
                        self.root.bell()

                elif tag == "L":
                    self.canvas.itemconfig(self.status_text_id,
                                            text="Electrodos desconectados",
                                            fill="yellow")
                    self.canvas.itemconfig(self.bpm_text_id, fill="yellow")

        if self.overlay:
            self.overlay.after(10, self.read_serial)


if __name__ == "__main__":
    MonitorApp()