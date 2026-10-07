import argparse
import json
import subprocess
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from adaptive_scheduler import AdaptiveAnchorScheduler, load_anchor_positions


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Estado del sistema UWB</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #0f1420; --card: #161d2e; --border: #262f45;
    --ok: #2ecc71; --warn: #f1c40f; --bad: #e74c3c; --muted: #7f8ca3;
    --text: #e7ecf5;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg); color: var(--text);
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    margin: 0; padding: 24px;
  }
  h1 { font-size: 20px; font-weight: 600; margin: 0 0 4px 0; }
  .subtitle { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
  .grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 16px; margin-bottom: 32px;
  }
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; padding: 16px 18px; position: relative;
  }
  .card-title { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; justify-content: space-between; }
  .card-title-left { display: flex; align-items: center; gap: 8px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
  .dot.ok { background: var(--ok); box-shadow: 0 0 8px var(--ok); }
  .dot.warn { background: var(--warn); box-shadow: 0 0 8px var(--warn); }
  .dot.bad { background: var(--bad); box-shadow: 0 0 8px var(--bad); }
  .dot.muted { background: var(--muted); }
  .row { display: flex; justify-content: space-between; font-size: 13px; margin-top: 8px; color: var(--muted); }
  .row b { color: var(--text); font-weight: 500; }
  .section-title { font-size: 14px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; margin: 24px 0 12px 0; }
  .error-msg { font-size: 12px; color: var(--bad); margin-top: 8px; word-break: break-all; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 600; }
  .badge.ok { background: rgba(46,204,113,0.15); color: var(--ok); }
  .badge.warn { background: rgba(241,196,15,0.15); color: var(--warn); }
  .badge.bad { background: rgba(231,76,60,0.15); color: var(--bad); }
  .badge.muted { background: rgba(127,140,163,0.15); color: var(--muted); }
  #last-update { color: var(--muted); font-size: 12px; }
  .reset-btn {
    background: rgba(231,76,60,0.12); color: var(--bad); border: 1px solid rgba(231,76,60,0.35);
    border-radius: 6px; padding: 3px 10px; font-size: 11px; cursor: pointer; font-weight: 600;
  }
  .reset-btn:hover { background: rgba(231,76,60,0.25); }
  .reset-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .history { display: flex; gap: 3px; margin-top: 10px; flex-wrap: wrap; }
  .hist-bar {
    width: 8px; height: 20px; border-radius: 2px; background: var(--border);
  }
  .hist-bar.success { background: var(--ok); }
  .hist-bar.fail { background: var(--bad); }
  .battery-bar-bg { width: 60px; height: 8px; border-radius: 4px; background: var(--border); overflow: hidden; display: inline-block; vertical-align: middle; margin-left: 6px; }
  .battery-bar-fill { height: 100%; border-radius: 4px; }
</style>
</head>
<body>
  <h1>Estado del sistema UWB</h1>
  <div class="subtitle">Se actualiza cada 2s &middot; <span id="last-update">cargando...</span></div>

  <p class="section-title">Placas (anchors)</p>
  <div class="grid" id="boards-grid"></div>

  <p class="section-title">Relojes (watches)</p>
  <div class="grid" id="watches-grid"></div>

<script>
const UMBRAL_SIN_EXITO_SEG = 60;
const UMBRAL_SIN_EXITO_CRITICO_SEG = 180;
const UMBRAL_FALLOS_CONSECUTIVOS_WARN = 3;
const UMBRAL_FALLOS_CONSECUTIVOS_BAD = 8;
const UMBRAL_ACTIVIDAD_RELOJ_SEG = 45;

function fmtAgo(ts, now) {
  if (!ts) return "nunca";
  const secs = Math.max(0, now - ts);
  if (secs < 60) return `hace ${Math.floor(secs)}s`;
  if (secs < 3600) return `hace ${Math.floor(secs/60)}m`;
  return `hace ${Math.floor(secs/3600)}h`;
}

function fmtUptime(uptimeS) {
  if (uptimeS === undefined || uptimeS === null) return "-";
  const s = parseInt(uptimeS);
  if (isNaN(s)) return "-";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s/60)}m`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
}

function boardStatus(health, now) {
  if (!health.last_attempt_at) return {level: "muted", label: "Sin datos"};
  const secsSinceSuccess = health.last_success_at ? (now - health.last_success_at) : Infinity;
  if (health.consecutive_failures >= UMBRAL_FALLOS_CONSECUTIVOS_BAD || secsSinceSuccess > UMBRAL_SIN_EXITO_CRITICO_SEG) {
    return {level: "bad", label: "Necesita revisión"};
  }
  if (health.consecutive_failures >= UMBRAL_FALLOS_CONSECUTIVOS_WARN || secsSinceSuccess > UMBRAL_SIN_EXITO_SEG) {
    return {level: "warn", label: "Degradado"};
  }
  return {level: "ok", label: "OK"};
}

function watchStatus(watch, now) {
  if (!watch.online) return {level: "bad", label: "Offline"};
  const lastActivity = Math.max(watch.last_presence || 0, watch.last_session_update || 0);
  if (lastActivity === 0) return {level: "warn", label: "Conectado, sin actividad todavia"};
  if ((now - lastActivity) > UMBRAL_ACTIVIDAD_RELOJ_SEG) return {level: "warn", label: "Sin actividad reciente"};
  return {level: "ok", label: "Online"};
}

function batteryColor(level) {
  if (level === undefined || level === null || level < 0) return "#7f8ca3";
  if (level <= 20) return "#e74c3c";
  if (level <= 50) return "#f1c40f";
  return "#2ecc71";
}

const UMBRAL_PICO_STALE_SEG = 30;  // respaldo por si el LWT fallara: si no llega nada en este tiempo, avisamos igualmente

function picoStatusBadge(health, now) {
  if (health.pico_reachable === null || health.pico_reachable === undefined) {
    return '<span class="badge muted">Sin datos todavía</span>';
  }
  if (!health.pico_reachable) {
    return '<span class="badge bad">Offline (LWT)</span>';
  }
  const stale = health.pico_last_seen && (now - health.pico_last_seen) > UMBRAL_PICO_STALE_SEG;
  if (stale) {
    return '<span class="badge warn">Sin novedades recientes</span>';
  }
  return '<span class="badge ok">Online</span>';
}

function renderHistory(history) {
  if (!history || history.length === 0) {
    return '<div class="row"><span>Histórico</span><b>sin sesiones todavía</b></div>';
  }
  const bars = history.map(h =>
    `<div class="hist-bar ${h.success ? 'success' : 'fail'}" title="${new Date(h.timestamp*1000).toLocaleTimeString()}: ${h.ok_count} OK / ${h.not_ok_count} NO_OK"></div>`
  ).join("");
  return `<div class="row"><span>Últimas ${history.length} sesiones</span></div><div class="history">${bars}</div>`;
}

async function resetBoard(anchor, btn) {
  btn.disabled = true;
  const originalText = btn.textContent;
  btn.textContent = "Reiniciando...";
  try {
    const res = await fetch(`/action/reset/${anchor}`, {method: "POST"});
    const data = await res.json();
    btn.textContent = data.ok ? "Reiniciado ✓" : "Error";
  } catch (e) {
    btn.textContent = "Error";
  }
  setTimeout(() => { btn.disabled = false; btn.textContent = originalText; }, 3000);
}

async function refresh() {
  try {
    const res = await fetch("/status");
    const data = await res.json();
    const now = data.server_time;

    document.getElementById("last-update").textContent =
      "última actualización: " + new Date().toLocaleTimeString();

    const boardsGrid = document.getElementById("boards-grid");
    boardsGrid.innerHTML = "";
    for (const [anchor, board] of Object.entries(data.boards)) {
      const health = data.board_health[anchor] || {};
      const st = boardStatus(health, now);
      const busySession = data.active_sessions_by_anchor[anchor];
      const total = (health.total_ok || 0) + (health.total_not_ok || 0);
      const pct = total > 0 ? Math.round(100 * health.total_ok / total) : null;

      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <div class="card-title">
          <div class="card-title-left"><span class="dot ${st.level}"></span> ${board.label} (${anchor})</div>
          <button class="reset-btn" data-anchor="${anchor}">Reiniciar placa</button>
        </div>
        <div class="row"><span>Estado</span><b><span class="badge ${st.level}">${st.label}</span></b></div>
        <div class="row"><span>Último intento</span><b>${fmtAgo(health.last_attempt_at, now)}</b></div>
        <div class="row"><span>Último éxito</span><b>${fmtAgo(health.last_success_at, now)}</b></div>
        <div class="row"><span>Última sesión</span><b>${health.last_ok_count ?? '-'} OK / ${health.last_not_ok_count ?? '-'} NO_OK</b></div>
        <div class="row"><span>Histórico (%)</span><b>${pct !== null ? pct + '% éxito (' + total + ' totales)' : 'sin datos'}</b></div>
        <div class="row"><span>Fallos seguidos</span><b>${health.consecutive_failures || 0}</b></div>
        <div class="row"><span>Sesión activa</span><b>${busySession || '-'}</b></div>
        ${health.last_error ? `<div class="error-msg">${health.last_error}</div>` : ''}
        ${renderHistory(health.session_history)}
        <div class="row" style="margin-top:14px; border-top: 1px solid var(--border); padding-top:8px;">
          <span>Pico</span><b>${picoStatusBadge(health, now)}</b>
        </div>
        <div class="row"><span>IP Pico</span><b>${health.pico_ip ?? '-'}</b></div>
        <div class="row"><span>Uptime Pico</span><b>${fmtUptime(health.pico_uptime_s)}</b></div>
        <div class="row"><span>WiFi RSSI</span><b>${health.pico_rssi ?? '-'} dBm</b></div>
        <div class="row"><span>Memoria libre</span><b>${health.pico_free_mem ?? '-'} bytes</b></div>
        <div class="row"><span>Alimentación DWM</span><b>${health.pico_power_state ?? '-'}</b></div>
        <div class="row"><span>Batería nodo</span><b>${health.pico_battery_percent !== null && health.pico_battery_percent !== undefined ? health.pico_battery_percent + '% (' + (health.pico_battery_voltage ?? '-') + 'V)' + (health.pico_battery_charging ? ' ⚡ cargando' : '') : '-'}
          <span class="battery-bar-bg"><span class="battery-bar-fill" style="width:${health.pico_battery_percent ?? 0}%; background:${batteryColor(health.pico_battery_percent)}; display:block;"></span></span>
        </b></div>
      `;
      boardsGrid.appendChild(card);
      card.querySelector(".reset-btn").addEventListener("click", (e) => resetBoard(anchor, e.target));
    }

    const watchesGrid = document.getElementById("watches-grid");
    watchesGrid.innerHTML = "";
    const watchIds = Object.keys(data.watches);
    if (watchIds.length === 0) {
      watchesGrid.innerHTML = '<div class="card"><span class="dot muted"></span> Ningún reloj visto todavía</div>';
    }
    for (const [watchId, watch] of Object.entries(data.watches)) {
      const st = watchStatus(watch, now);
      const activeSession = data.active_sessions_by_watch[watchId];
      const battLevel = watch.battery_level;
      const battStr = (battLevel === undefined || battLevel === null || battLevel < 0) ? "-" : `${battLevel}%`;
      const battWidth = (battLevel && battLevel >= 0) ? Math.min(100, battLevel) : 0;
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <div class="card-title"><span class="dot ${st.level}"></span> ${watchId}</div>
        <div class="row"><span>Estado</span><b><span class="badge ${st.level}">${st.label}</span></b></div>
        <div class="row"><span>Última presencia (conexión)</span><b>${fmtAgo(watch.last_presence, now)}</b></div>
        <div class="row"><span>Última actividad (sesión)</span><b>${fmtAgo(watch.last_session_update, now)}</b></div>
        <div class="row"><span>Batería</span><b>${battStr}
          <span class="battery-bar-bg"><span class="battery-bar-fill" style="width:${battWidth}%; background:${batteryColor(battLevel)}; display:block;"></span></span>
        </b></div>
        <div class="row"><span>Anchor actual</span><b>${watch.current_anchor || '-'}</b></div>
        <div class="row"><span>MAC UWB</span><b>${watch.uwb_mac || '-'}</b></div>
        <div class="row"><span>Sesión activa</span><b>${activeSession || '-'}</b></div>
      `;
      watchesGrid.appendChild(card);
    }
  } catch (e) {
    document.getElementById("last-update").textContent = "Error conectando con el orquestador: " + e;
  }
}

refresh();
setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class UwbOrchestratorMulti:
    def __init__(
        self,
        broker: str,
        port: int,
        boards: list[dict[str, Any]],
        ranging_time: int = 10,
        prepare_timeout: int = 15,
        data_dir: str = "data",
        run_script: str = "run_fira_twr.py",
        python_cmd: str = "python",
        round_robin_mode: str = "adaptive",
        anchor_positions_file: str | None = None,
        adaptive_window_seconds: int = 15,
        adaptive_neighbor_scale: float = 0.30,
        adaptive_temperature: float = 0.65,
        adaptive_minimum_probability: float = 0.04,
        adaptive_temporal_decay: float = 0.9,
        adaptive_max_distance_m: float = 10.0,
        adaptive_evidence_power: float = 0.5,
        adaptive_selection_mode: str = "rejection",
        adaptive_acceptance_floor: float = 0.1,
        adaptive_evidence_transform: str = "sigmoid",
        adaptive_sigmoid_slope: float = 4.0,
        adaptive_proximity_weight: float = 5.0,
        adaptive_fairness_weight: float = 1.0,
        adaptive_history_aggregation: str = "per_anchor",
    ) -> None:
        self.broker = broker
        self.port = port
        self.ranging_time = ranging_time
        self.prepare_timeout = prepare_timeout
        self.data_dir = Path(data_dir)
        self.run_script = run_script
        self.python_cmd = python_cmd

        self.round_robin_mode = round_robin_mode.lower().strip()
        if self.round_robin_mode not in {"strict", "flexible", "adaptive"}:
            raise ValueError("round_robin_mode debe ser 'strict', 'flexible' o 'adaptive'")

        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.lock = threading.RLock()

        self.boards: dict[str, dict[str, Any]] = {}
        for board in boards:
            anchor = str(board["anchor"]).upper()
            self.boards[anchor] = {
                "anchor": anchor,
                "my_mac": str(board["my_mac"]).upper(),
                "serial_port": str(board["serial_port"]),
                "label": str(board.get("label", anchor)),
            }

        self.anchor_order: list[str] = list(self.boards.keys())
        self.next_anchor_index_by_watch: dict[str, int] = {}
        default_positions = Path(__file__).resolve().parents[3] / "fingerprinting" / "data" / "sit_placas.csv"
        positions_path = Path(anchor_positions_file) if anchor_positions_file else default_positions
        positions = load_anchor_positions(positions_path) if positions_path.is_file() else {}
        if self.round_robin_mode == "adaptive" and not positions:
            print(f"[WARN] No se encontró {positions_path}; la política adaptativa funcionará sin vecindad espacial")
        self.adaptive_scheduler = AdaptiveAnchorScheduler(
            self.anchor_order,
            positions,
            window_seconds=adaptive_window_seconds,
            neighbor_scale=adaptive_neighbor_scale,
            temperature=adaptive_temperature,
            minimum_probability=adaptive_minimum_probability,
            temporal_decay=adaptive_temporal_decay,
            max_distance_m=adaptive_max_distance_m,
            evidence_power=adaptive_evidence_power,
            selection_mode=adaptive_selection_mode,
            acceptance_floor=adaptive_acceptance_floor,
            evidence_transform=adaptive_evidence_transform,
            sigmoid_slope=adaptive_sigmoid_slope,
            proximity_weight=adaptive_proximity_weight,
            fairness_weight=adaptive_fairness_weight,
            history_aggregation=adaptive_history_aggregation,
        )

        # --- Salud/monitorización por placa (para el dashboard) ---
        self.board_health: dict[str, dict[str, Any]] = {
            anchor: {
                "total_ok": 0,
                "total_not_ok": 0,
                "last_attempt_at": None,
                "last_success_at": None,
                "consecutive_failures": 0,
                "last_error": None,
                "last_ok_count": None,
                "last_not_ok_count": None,
                # Historico de las ultimas sesiones (mas reciente al final)
                "session_history": [],
                # Salud reportada por la propia Pico, via MQTT
                # (uwb/pico/{anchor}/status, incluido el LWT si se cae)
                "pico_reachable": None,
                "pico_last_seen": None,
                "pico_ip": None,
                "pico_uptime_s": None,
                "pico_rssi": None,
                "pico_free_mem": None,
                "pico_power_state": None,
                "pico_battery_voltage": None,
                "pico_battery_percent": None,
                "pico_battery_charging": None,
            }
            for anchor in self.boards
        }
        self.health_lock = threading.RLock()
        self.http_server: ThreadingHTTPServer | None = None
        self.http_thread: threading.Thread | None = None

        self.watches: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}

        self.active_sessions_by_watch: dict[str, str] = {}
        self.active_sessions_by_anchor: dict[str, str] = {}
        self.ranging_threads_by_anchor: dict[str, threading.Thread] = {}
        self.prepare_timers_by_session: dict[str, threading.Timer] = {}

        self.scheduler_thread: threading.Thread | None = None
        self.scheduler_stop_event = threading.Event()

        # Umbral para considerar que una Pico esta "viva": si no llega
        # ningun mensaje de estado (via MQTT) en este tiempo, se marca
        # como no accesible (ademas de que el LWT del broker deberia
        # marcarla offline automaticamente si se cae de forma anomala).
        self.pico_stale_after_seconds = 30.0

    def reset_board(self, anchor: str) -> bool:
        """Fuerza un power-cycle del DWM de un anchor concreto, publicando
        el comando en el topic MQTT de control de su Pico. Al ser MQTT
        (fire-and-forget), no esperamos una respuesta sincrona: el efecto
        se vera reflejado en el siguiente mensaje de estado que publique
        la Pico."""
        anchor = anchor.upper()
        if anchor not in self.boards:
            print(f"[CTRL] Anchor desconocido: {anchor}")
            return False

        command_topic = f"uwb/pico/{anchor}/commands"
        payload = {"action": "POWER_CYCLE"}
        self.client.publish(command_topic, json.dumps(payload))
        print(f"[CTRL] Publicado POWER_CYCLE en {command_topic}")
        return True

    def handle_pico_status(self, topic: str, payload: dict) -> None:
        """Procesa los mensajes de uwb/pico/{anchor}/status, incluido el
        mensaje offline retenido por el Last Will del broker si la Pico
        se cae de forma anomala."""
        parts = topic.split("/")
        if len(parts) < 3:
            return
        anchor = (payload.get("anchor") or parts[2]).upper()

        if anchor not in self.board_health:
            return  # Pico de un anchor que no tenemos configurado

        state = payload.get("state")

        with self.health_lock:
            health = self.board_health[anchor]
            health["pico_last_seen"] = time.time()
            health["pico_reachable"] = (state == "online")
            # La IP viene tanto en "online" como en el LWT "offline" (ultima conocida)
            if payload.get("ip"):
                health["pico_ip"] = payload.get("ip")
            if state == "online":
                health["pico_uptime_s"] = payload.get("uptime_s")
                health["pico_rssi"] = payload.get("rssi")
                health["pico_free_mem"] = payload.get("free_mem")
                health["pico_power_state"] = payload.get("power")
                health["pico_battery_voltage"] = payload.get("battery_voltage")
                health["pico_battery_percent"] = payload.get("battery_percent")
                health["pico_battery_charging"] = payload.get("battery_charging")

        print(f"[PICO] {anchor}: state={state} ip={payload.get('ip')} rssi={payload.get('rssi')} "
              f"uptime={payload.get('uptime_s')} "
              f"battery={payload.get('battery_percent')}% ({payload.get('battery_voltage')}V)")

    def key_file_time(self) -> str:
        return str(int(time.time() / (60 * 60 * 24)))

    def connect(self) -> None:
        print(f"[MQTT] Conectando a {self.broker}:{self.port}...")
        self.client.connect(self.broker, self.port, 60)

    def loop_forever(self) -> None:
        self.client.loop_forever()

    def on_connect(self, client, userdata, flags, rc):
        print(f"[MQTT] Conectado con código {rc}")
        client.subscribe("uwb/presence/+")
        client.subscribe("uwb/session/+")
        client.subscribe("uwb/pico/+/status")
        print("[MQTT] Suscrito a uwb/presence/+")
        print("[MQTT] Suscrito a uwb/session/+")
        print("[MQTT] Suscrito a uwb/pico/+/status")
        print(f"[INFO] Orquestador listo | round_robin_mode={self.round_robin_mode}")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        raw_payload = msg.payload.decode(errors="replace").strip()

        print(f"[MQTT] Mensaje en {topic}: {raw_payload}")

        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            print("[WARN] Payload no es JSON válido, ignorando")
            return

        if topic.startswith("uwb/presence/"):
            self.handle_presence(topic, payload)
        elif topic.startswith("uwb/session/"):
            self.handle_session_update(topic, payload)
        elif topic.startswith("uwb/pico/") and topic.endswith("/status"):
            self.handle_pico_status(topic, payload)

    def handle_presence(self, topic: str, payload: dict) -> None:
        watch_id = payload.get("watch_id") or topic.split("/")[-1]
        state = payload.get("state", "unknown")
        battery_level = payload.get("battery_level")

        with self.lock:
            watch = self.watches.setdefault(watch_id, {})
            watch["online"] = state == "online"
            watch["presence_state"] = state
            watch["last_presence"] = time.time()
            if battery_level is not None:
                watch["battery_level"] = battery_level

        print(f"[STATE] {watch_id}: online={state == 'online'} state={state} battery={battery_level}")

    def handle_session_update(self, topic: str, payload: dict) -> None:
        watch_id = payload.get("watch_id") or topic.split("/")[-1]
        state = payload.get("state")
        uwb_mac = payload.get("uwb_mac")
        anchor = (payload.get("anchor") or "").upper()
        session_id = payload.get("session_id")

        with self.lock:
            watch = self.watches.setdefault(watch_id, {})
            watch["last_session_update"] = time.time()
            if uwb_mac:
                watch["uwb_mac"] = uwb_mac.upper()
            if anchor:
                watch["current_anchor"] = anchor
            if state:
                watch["session_state"] = state
            if session_id:
                watch["session_id"] = session_id

            active_session_id = self.active_sessions_by_watch.get(watch_id)
            active_session = self.sessions.get(active_session_id) if active_session_id else None

        if not active_session:
            print(f"[INFO] No hay sesión activa para {watch_id}")
            return

        matches_active_session = False
        if session_id and session_id == active_session["session_id"]:
            matches_active_session = True
        elif (
            not session_id
            and watch_id == active_session["watch_id"]
            and anchor == active_session["anchor"]
        ):
            matches_active_session = True
            print("[WARN] Mensaje sin session_id: correlación por watch_id + anchor")

        if not matches_active_session:
            print("[INFO] Update de sesión ignorado: no corresponde a la sesión activa del reloj")
            return

        if state != "prepared":
            print(f"[INFO] Estado de sesión recibido, pero no es prepared: {state}")
            return

        if not uwb_mac:
            print("[WARN] Sesión preparada sin uwb_mac")
            return

        self.cancel_prepare_timer(active_session["session_id"])

        with self.lock:
            active_session["watch_mac"] = uwb_mac.upper()
            active_session["status"] = "prepared"
            active_session["updated_at"] = time.time()

        print(
            f"[SESSION] {active_session['session_id']} preparada: "
            f"watch={watch_id} anchor={active_session['anchor']} uwb_mac={uwb_mac.upper()}"
        )

        self.launch_ranging_if_needed(active_session["session_id"])

    def get_free_anchor(self) -> str | None:
        with self.lock:
            busy_anchors = set(self.active_sessions_by_anchor.keys())
            for anchor in self.boards:
                if anchor not in busy_anchors:
                    return anchor
        return None

    def is_watch_free(self, watch_id: str) -> bool:
        with self.lock:
            return watch_id not in self.active_sessions_by_watch

    def get_next_anchor_for_watch_strict(self, watch_id: str) -> str | None:
        with self.lock:
            if not self.anchor_order:
                return None

            idx = self.next_anchor_index_by_watch.get(watch_id, 0)
            anchor = self.anchor_order[idx]

            if anchor in self.active_sessions_by_anchor:
                return None

            self.next_anchor_index_by_watch[watch_id] = (idx + 1) % len(self.anchor_order)
            return anchor

    def get_next_anchor_for_watch_flexible(self, watch_id: str) -> str | None:
        with self.lock:
            if not self.anchor_order:
                return None

            start_idx = self.next_anchor_index_by_watch.get(watch_id, 0)
            total = len(self.anchor_order)

            for offset in range(total):
                idx = (start_idx + offset) % total
                anchor = self.anchor_order[idx]

                if anchor not in self.active_sessions_by_anchor:
                    self.next_anchor_index_by_watch[watch_id] = (idx + 1) % total
                    return anchor

            return None

    def get_next_anchor_for_watch_adaptive(self, watch_id: str) -> str | None:
        with self.lock:
            free_anchors = [anchor for anchor in self.anchor_order if anchor not in self.active_sessions_by_anchor]
            anchor, probabilities = self.adaptive_scheduler.choose(watch_id, free_anchors)
        if anchor:
            compact = ", ".join(f"{key}={value:.2f}" for key, value in probabilities.items())
            print(f"[SCHED] {watch_id}: elegido {anchor}; probabilidades {compact}")
        return anchor

    def get_next_anchor_for_watch(self, watch_id: str) -> str | None:
        if self.round_robin_mode == "strict":
            return self.get_next_anchor_for_watch_strict(watch_id)
        if self.round_robin_mode == "flexible":
            return self.get_next_anchor_for_watch_flexible(watch_id)
        return self.get_next_anchor_for_watch_adaptive(watch_id)

    def start_session(self, watch_id: str, preferred_anchor: str | None = None) -> str | None:
        preferred_anchor = preferred_anchor.upper() if preferred_anchor else None

        with self.lock:
            if watch_id in self.active_sessions_by_watch:
                print(f"[WARN] El reloj {watch_id} ya tiene una sesión activa")
                return None

            anchor = preferred_anchor or self.get_free_anchor()
            if not anchor:
                print(f"[WARN] No hay placas libres para iniciar sesión con {watch_id}")
                return None

            if anchor not in self.boards:
                print(f"[WARN] Anchor desconocido: {anchor}")
                return None

            if anchor in self.active_sessions_by_anchor:
                print(f"[WARN] La placa {anchor} ya tiene una sesión activa")
                return None

            session_id = f"sess_{uuid.uuid4().hex[:8]}"
            board = self.boards[anchor]
            session = {
                "session_id": session_id,
                "watch_id": watch_id,
                "anchor": anchor,
                "board_label": board["label"],
                "board_mac": board["my_mac"],
                "serial_port": board["serial_port"],
                "status": "waiting_watch_prepare",
                "watch_mac": None,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            self.sessions[session_id] = session
            self.active_sessions_by_watch[watch_id] = session_id
            self.active_sessions_by_anchor[anchor] = session_id

        command_topic = f"uwb/commands/{watch_id}"
        payload = {
            "action": "start",
            "anchor": anchor,
            "session_id": session_id,
        }

        self.client.publish(command_topic, json.dumps(payload))
        print(f"[SESSION] Creada {session_id} para {watch_id} usando {anchor}")
        print(f"[MQTT] Publicado en {command_topic}: {payload}")

        timer = threading.Timer(self.prepare_timeout, self.handle_prepare_timeout, args=(session_id,))
        timer.daemon = True
        with self.lock:
            self.prepare_timers_by_session[session_id] = timer
        timer.start()

        return session_id

    def handle_prepare_timeout(self, session_id: str) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                return
            if session["status"] != "waiting_watch_prepare":
                return

            session["status"] = "prepare_timeout"
            session["updated_at"] = time.time()
            watch_id = session["watch_id"]
            anchor = session["anchor"]

        print(f"[TIMEOUT] La sesión {session_id} no llegó a prepared en {self.prepare_timeout}s")
        self.publish_stop(watch_id, session_id)
        self.release_session_resources(session_id, watch_id, anchor)

    def cancel_prepare_timer(self, session_id: str) -> None:
        with self.lock:
            timer = self.prepare_timers_by_session.pop(session_id, None)
        if timer:
            timer.cancel()

    def launch_ranging_if_needed(self, session_id: str) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                print(f"[WARN] No existe la sesión {session_id}")
                return

            if session["status"] != "prepared":
                print(f"[INFO] La sesión {session_id} todavía no está preparada")
                return

            anchor = session["anchor"]
            existing_thread = self.ranging_threads_by_anchor.get(anchor)
            if existing_thread and existing_thread.is_alive():
                print(f"[WARN] Ya hay un ranging en ejecución en la placa {anchor}")
                return

            session["status"] = "ranging_started"
            watch_mac = session["watch_mac"]

        thread = threading.Thread(
            target=self.run_ranging_session,
            args=(session_id, watch_mac),
            daemon=True,
        )
        with self.lock:
            self.ranging_threads_by_anchor[anchor] = thread
        thread.start()

    def run_ranging_session(self, session_id: str, dest_mac: str) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                print(f"[WARN] La sesión {session_id} ya no existe")
                return
            watch_id = session["watch_id"]
            anchor = session["anchor"]
            serial_port = session["serial_port"]
            board_mac = session["board_mac"]

        print(f"[RANGING] Lanzando sesión {session_id} hacia {dest_mac} con anchor {anchor}")

        with self.health_lock:
            self.board_health[anchor]["last_attempt_at"] = time.time()

        cmd = [
            self.python_cmd,
            self.run_script,
            "-p",
            serial_port,
            "-t",
            str(self.ranging_time),
            "--mac",
            board_mac,
            "--dest-mac",
            dest_mac,
            "--controlee",
        ]

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            filename = self.data_dir / f"{self.key_file_time()}.{watch_id}.tsv"

            # Leer en streaming: antes subprocess.run agrupaba diez segundos
            # de ranging en un único instante al finalizar el proceso.
            cmd.insert(1, "-u")
            lines = []
            errors = []
            received_measurement = False
            block_ok = False
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, bufsize=1) as process:
                def collect_errors():
                    for error_line in process.stderr:
                        errors.append(error_line)

                stderr_thread = threading.Thread(target=collect_errors, daemon=True)
                stderr_thread.start()
                for line in process.stdout:
                    lines.append(line)
                    stripped = line.strip()
                    if stripped.startswith("# Ranging Data:"):
                        block_ok = False
                    elif "status:" in stripped:
                        block_ok = "Ok" in stripped
                        if not block_ok:
                            with self.lock:
                                self.adaptive_scheduler.record(watch_id, anchor, [])
                            received_measurement = True
                    elif block_ok and "distance:" in stripped:
                        try:
                            value = float(stripped.split("distance:", 1)[1].replace("cm", "").strip())
                        except ValueError:
                            continue
                        with self.lock:
                            self.adaptive_scheduler.record(watch_id, anchor, [value])
                        received_measurement = True
                returncode = process.wait()
                stderr_thread.join()
            result = subprocess.CompletedProcess(cmd, returncode, "".join(lines), "".join(errors))

            output = result.stdout.splitlines()
            if result.stderr:
                print("[WARN] STDERR:", result.stderr.strip())

            blocks = []
            current_block = []

            for line in output:
                line = line.strip()

                if line.startswith("# Ranging Data:"):
                    if current_block:
                        blocks.append(current_block)
                    current_block = [line]
                elif current_block:
                    current_block.append(line)

            if current_block:
                blocks.append(current_block)

            ok_count = 0
            not_ok_count = 0
            distances: list[float] = []

            with open(filename, "a", encoding="utf-8") as datafile:
                if filename.stat().st_size == 0:
                    datafile.write(
                        "timestamp\tsession_id\twatch_id\tanchor\tboard_mac\tserial_port\twatch_mac\tdistance_cm\trssi_dbm\n"
                    )

                for block in blocks:
                    status_line = next((l for l in block if "status:" in l), "")
                    if "Ok" in status_line:
                        dist_line = next((l for l in block if "distance:" in l), "")
                        rssi_line = next((l for l in block if "rssi:" in l), "")

                        try:
                            distance = float(
                                dist_line.split(":")[1].replace("cm", "").strip()
                            )
                            rssi = float(
                                rssi_line.split(":")[1].replace("dBm", "").strip()
                            )
                            timestamp = datetime.now().isoformat()

                            datafile.write(
                                f"{timestamp}\t{session_id}\t{watch_id}\t{anchor}\t{board_mac}\t{serial_port}\t"
                                f"{dest_mac}\t{distance}\t{rssi}\n"
                            )
                            distances.append(distance)
                            ok_count += 1
                        except Exception as exc:
                            print(f"[WARN] Error al procesar bloque válido: {exc}")
                    else:
                        not_ok_count += 1

                # Una sesión sin distancia válida también queda representada en el
                # log y en M como -1: es información para el planificador.
                if not distances:
                    timestamp = datetime.now().isoformat()
                    datafile.write(
                        f"{timestamp}\t{session_id}\t{watch_id}\t{anchor}\t{board_mac}\t{serial_port}\t"
                        f"{dest_mac}\t-1\t-1\n"
                    )

            if not received_measurement:
                with self.lock:
                    self.adaptive_scheduler.record(watch_id, anchor, [])
            print(f"[SCHED] Ventana M actualizada durante la sesión: watch={watch_id} anchor={anchor}")

            print(f"\n[END] Sesión completada: {session_id}")
            print(f"[INFO] Archivo: {filename}")
            print(f"[INFO] Mediciones OK guardadas: {ok_count}")
            print(f"[INFO] Mediciones no OK detectadas: {not_ok_count}\n")

            with self.health_lock:
                health = self.board_health[anchor]
                health["total_ok"] += ok_count
                health["total_not_ok"] += not_ok_count
                health["last_ok_count"] = ok_count
                health["last_not_ok_count"] = not_ok_count
                if ok_count > 0:
                    health["last_success_at"] = time.time()
                    health["consecutive_failures"] = 0
                    health["last_error"] = None
                else:
                    health["consecutive_failures"] += 1
                    health["last_error"] = (
                        result.stderr.strip() if result.stderr else "Sin mediciones OK"
                    )

                health["session_history"].append({
                    "session_id": session_id,
                    "watch_id": watch_id,
                    "timestamp": time.time(),
                    "ok_count": ok_count,
                    "not_ok_count": not_ok_count,
                    "success": ok_count > 0,
                })
                # Nos quedamos solo con las ultimas 20 para no crecer sin limite
                health["session_history"] = health["session_history"][-20:]

            self.publish_stop(watch_id, session_id)

            with self.lock:
                if session_id in self.sessions:
                    self.sessions[session_id]["status"] = "completed"
                    self.sessions[session_id]["updated_at"] = time.time()

        except Exception as exc:
            print(f"[ERROR] Error ejecutando ranging para {session_id}: {exc}")
            with self.health_lock:
                health = self.board_health[anchor]
                health["consecutive_failures"] += 1
                health["last_error"] = str(exc)
                health["session_history"].append({
                    "session_id": session_id,
                    "watch_id": watch_id,
                    "timestamp": time.time(),
                    "ok_count": 0,
                    "not_ok_count": 0,
                    "success": False,
                    "error": str(exc),
                })
                health["session_history"] = health["session_history"][-20:]
            with self.lock:
                self.adaptive_scheduler.record(watch_id, anchor, [])
            with self.lock:
                if session_id in self.sessions:
                    self.sessions[session_id]["status"] = "error"
                    self.sessions[session_id]["updated_at"] = time.time()
        finally:
            self.release_session_resources(session_id, watch_id, anchor)
            with self.lock:
                self.ranging_threads_by_anchor.pop(anchor, None)

    def publish_stop(self, watch_id: str, session_id: str) -> None:
        stop_topic = f"uwb/commands/{watch_id}"
        stop_payload = {"action": "stop", "session_id": session_id}
        self.client.publish(stop_topic, json.dumps(stop_payload))
        print(f"[MQTT] Publicado STOP en {stop_topic}: {stop_payload}")

    def release_session_resources(self, session_id: str, watch_id: str, anchor: str) -> None:
        self.cancel_prepare_timer(session_id)
        with self.lock:
            if self.active_sessions_by_watch.get(watch_id) == session_id:
                self.active_sessions_by_watch.pop(watch_id, None)
            if self.active_sessions_by_anchor.get(anchor) == session_id:
                self.active_sessions_by_anchor.pop(anchor, None)

    def start_cycle_scheduler(
        self,
        watches: list[str],
        loop_interval_seconds: float = 1.0,
        launch_stagger_seconds: float = 0.3,
    ) -> None:
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            print("[WARN] El scheduler cíclico ya está en ejecución")
            return

        self.scheduler_stop_event.clear()

        def _scheduler_loop():
            print("[SCHED] Scheduler cíclico iniciado")
            watch_cursor = 0
            while not self.scheduler_stop_event.is_set():
                # Cambiar el reloj que abre cada vuelta reparte las placas libres
                # cuando varios relojes compiten por ellas.
                ordered_watches = watches[watch_cursor:] + watches[:watch_cursor]
                watch_cursor = (watch_cursor + 1) % len(watches) if watches else 0
                for watch_id in ordered_watches:
                    if self.scheduler_stop_event.is_set():
                        break

                    with self.lock:
                        watch_busy = watch_id in self.active_sessions_by_watch

                    if watch_busy:
                        free_anchor = None
                    else:
                        free_anchor = self.get_next_anchor_for_watch(watch_id)

                    started = False
                    if not watch_busy and free_anchor:
                        session_id = self.start_session(watch_id, free_anchor)
                        if session_id:
                            started = True

                    if started and launch_stagger_seconds > 0:
                        time.sleep(launch_stagger_seconds)

                if loop_interval_seconds > 0:
                    time.sleep(loop_interval_seconds)

            print("[SCHED] Scheduler cíclico detenido")

        self.scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
        self.scheduler_thread.start()

    def stop_cycle_scheduler(self) -> None:
        self.scheduler_stop_event.set()

    def status_snapshot(self) -> dict[str, Any]:
        with self.lock, self.health_lock:
            return {
                "boards": self.boards,
                "watches": self.watches,
                "active_sessions_by_watch": self.active_sessions_by_watch,
                "active_sessions_by_anchor": self.active_sessions_by_anchor,
                "sessions": self.sessions,
                "round_robin_mode": self.round_robin_mode,
                "next_anchor_index_by_watch": self.next_anchor_index_by_watch,
                "adaptive_scheduler": self.adaptive_scheduler.snapshot(),
                "board_health": self.board_health,
                "server_time": time.time(),
            }

    def start_dashboard_server(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        orchestrator = self

        class DashboardHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # silenciamos el log de acceso, ya tenemos suficiente ruido

            def do_GET(self):
                if self.path == "/status":
                    payload = json.dumps(orchestrator.status_snapshot()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(payload)
                elif self.path in ("/", "/index.html"):
                    payload = DASHBOARD_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path.startswith("/action/reset/"):
                    anchor = self.path[len("/action/reset/"):].upper()
                    if anchor not in orchestrator.boards:
                        self.send_response(404)
                        self.end_headers()
                        self.wfile.write(b'{"ok": false, "error": "anchor desconocido"}')
                        return

                    success = orchestrator.reset_board(anchor)
                    payload = json.dumps({"ok": success}).encode("utf-8")
                    self.send_response(200 if success else 500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_response(404)
                    self.end_headers()

        self.http_server = ThreadingHTTPServer((host, port), DashboardHandler)
        self.http_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.http_thread.start()
        print(f"[DASHBOARD] Servidor arrancado en http://{host}:{port}/  (o http://<IP_DE_ESTE_PC>:{port}/)")

    def stop_dashboard_server(self) -> None:
        if self.http_server:
            self.http_server.shutdown()


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Orquestador MQTT-UWB multi reloj / multi placa")
    parser.add_argument(
        "--config",
        default="orquestador_config.json",
        help="Archivo JSON con toda la configuración del orquestador",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    mqtt_cfg = config.get("mqtt", {})
    broker = mqtt_cfg.get("broker", "192.168.18.22")
    port = int(mqtt_cfg.get("port", 1883))

    orchestrator_cfg = config.get("orchestrator", {})
    ranging_time = int(orchestrator_cfg.get("ranging_time", 10))
    prepare_timeout = int(orchestrator_cfg.get("prepare_timeout", 15))
    data_dir = orchestrator_cfg.get("data_dir", "data")
    run_script = orchestrator_cfg.get("run_script", "run_fira_twr.py")
    python_cmd = orchestrator_cfg.get("python_cmd", "python")
    round_robin_mode = str(orchestrator_cfg.get("round_robin_mode", "adaptive"))
    anchor_positions_file = orchestrator_cfg.get("anchor_positions_file")
    adaptive_cfg = orchestrator_cfg.get("adaptive_scheduler", {})

    boards = config.get("boards", [])
    if not boards:
        raise ValueError("La configuración debe incluir al menos una placa en 'boards'")

    cycle_cfg = config.get("cycle", {})
    cycle_enabled = bool(cycle_cfg.get("enabled", False))
    cycle_watches = cycle_cfg.get("watches", [])
    loop_interval_seconds = float(cycle_cfg.get("loop_interval_seconds", 1.0))
    launch_stagger_seconds = float(cycle_cfg.get("launch_stagger_seconds", 0.3))

    dashboard_cfg = config.get("dashboard", {})
    dashboard_enabled = bool(dashboard_cfg.get("enabled", True))
    dashboard_host = dashboard_cfg.get("host", "0.0.0.0")
    dashboard_port = int(dashboard_cfg.get("port", 8080))

    orchestrator = UwbOrchestratorMulti(
        broker=broker,
        port=port,
        boards=boards,
        ranging_time=ranging_time,
        prepare_timeout=prepare_timeout,
        data_dir=data_dir,
        run_script=run_script,
        python_cmd=python_cmd,
        round_robin_mode=round_robin_mode,
        anchor_positions_file=anchor_positions_file,
        adaptive_window_seconds=int(adaptive_cfg.get("window_seconds", 15)),
        adaptive_neighbor_scale=float(adaptive_cfg.get("neighbor_scale", 0.30)),
        adaptive_temperature=float(adaptive_cfg.get("temperature", 0.65)),
        adaptive_minimum_probability=float(adaptive_cfg.get("minimum_probability", 0.04)),
        adaptive_temporal_decay=float(adaptive_cfg.get("temporal_decay", 0.9)),
        adaptive_max_distance_m=float(adaptive_cfg.get("max_distance_m", 10.0)),
        adaptive_evidence_power=float(adaptive_cfg.get("evidence_power", 0.5)),
        adaptive_selection_mode=str(adaptive_cfg.get("selection_mode", "rejection")),
        adaptive_acceptance_floor=float(adaptive_cfg.get("acceptance_floor", 0.1)),
        adaptive_evidence_transform=str(adaptive_cfg.get("evidence_transform", "sigmoid")),
        adaptive_sigmoid_slope=float(adaptive_cfg.get("sigmoid_slope", 4.0)),
        adaptive_proximity_weight=float(adaptive_cfg.get("w_cercania", 5.0)),
        adaptive_fairness_weight=float(adaptive_cfg.get("w_equidad", 1.0)),
        adaptive_history_aggregation=str(adaptive_cfg.get("history_aggregation", "per_anchor")),
    )

    try:
        orchestrator.connect()

        if dashboard_enabled:
            orchestrator.start_dashboard_server(host=dashboard_host, port=dashboard_port)

        # La salud de las Picos ahora llega de forma pasiva via MQTT
        # (uwb/pico/+/status), no hace falta arrancar ningun monitor activo.

        if cycle_enabled:
            if not cycle_watches:
                raise ValueError("Si cycle.enabled=true, debes indicar cycle.watches")
            orchestrator.start_cycle_scheduler(
                watches=cycle_watches,
                loop_interval_seconds=loop_interval_seconds,
                launch_stagger_seconds=launch_stagger_seconds,
            )

        orchestrator.loop_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Saliendo por teclado")
        orchestrator.stop_cycle_scheduler()
        orchestrator.stop_dashboard_server()
    except Exception as exc:
        print(f"[ERROR] No se pudo arrancar el orquestador: {exc}")


if __name__ == "__main__":
    main()
