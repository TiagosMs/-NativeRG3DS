#!/usr/bin/env python3
"""
NativeRG3DS Studio - GUI Web para Portar e Recompilar Jogos de Nintendo 3DS para Anbernic RG DS (ROCKNIX)
Executa um servidor local em Python puro (sem dependências externas) e abre no navegador.
"""

import http.server
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
BUILD_DIR = REPO_DIR / "build"
TITLES_DIR = REPO_DIR / "titles"
DEFAULT_IP = "192.168.0.100"

# Estado global da compilação
build_state = {
    "running": False,
    "progress": 0,
    "step": "",
    "status": "idle",
    "logs": [],
    "error": None,
    "success": False
}
build_lock = threading.Lock()

def add_log(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with build_lock:
        build_state["logs"].append(line)
        if len(build_state["logs"]) > 2000:
            build_state["logs"].pop(0)

def set_progress(percent: int, step_desc: str):
    with build_lock:
        build_state["progress"] = percent
        build_state["step"] = step_desc
    add_log(f"--- {step_desc} ({percent}%) ---")

def inspect_rom_file(path_str: str) -> dict:
    path = Path(path_str).expanduser()
    if not path.is_file():
        return {"error": f"Arquivo não encontrado: {path_str}"}

    file_size_mb = path.stat().st_size / (1024 * 1024)
    try:
        with open(path, "rb") as f:
            header = f.read(0x200)
            if len(header) < 0x200 or header[0x100:0x104] != b"NCSD":
                return {
                    "error": "O arquivo não é uma imagem .3ds/.cci válida (assinatura NCSD ausente). Se for .cia, converta ou extraia primeiro.",
                    "size_mb": round(file_size_mb, 1)
                }
            title_id = struct.unpack_from("<Q", header, 0x108)[0]
            first_offset = struct.unpack_from("<I", header, 0x120)[0] * 0x200
            f.seek(first_offset)
            ncch = f.read(0x200)
            if len(ncch) < 0x200 or ncch[0x100:0x104] != b"NCCH":
                return {"error": "A partição primária da ROM não é um NCCH válido."}
            prod_code = ncch[0x150:0x160].rstrip(b"\x00").decode("latin1", errors="replace")
            exheader = f.read(32)
            is_decrypted = bool((ncch[0x18F] & 0x4) or exheader.startswith(b"CtrApp") or (len(exheader) >= 20 and struct.unpack_from("<I", exheader, 0x10)[0] == 0x100000))
            
            title_id_hex = f"{title_id:016X}"
            
            # Tentar achar o nome em titles/
            known_name = ""
            profile_file = TITLES_DIR / title_id_hex / "profile.toml"
            if profile_file.is_file():
                content = profile_file.read_text(encoding="utf-8")
                m = re.search(r'name\s*=\s*"([^"]+)"', content)
                if m:
                    known_name = m.group(1)
            
            if not known_name:
                # Dedução do nome do arquivo
                stem = path.stem
                # Limpar coisas como (Europe) (En,Fr) Decrypted
                clean_name = re.sub(r"\([^)]*\)", "", stem)
                clean_name = re.sub(r"\[[^\]]*\]", "", clean_name)
                clean_name = re.sub(r"(Decrypted|v[0-9.]+|Rev\s*[0-9]+)", "", clean_name, flags=re.IGNORECASE)
                known_name = clean_name.strip()

            profile_options = load_profile_options(title_id_hex)

            return {
                "success": True,
                "title_id": title_id_hex,
                "product_code": prod_code,
                "name": known_name,
                "decrypted": is_decrypted,
                "size_mb": round(file_size_mb, 1),
                "path": str(path.resolve()),
                "profile": profile_options
            }
    except Exception as e:
        return {"error": f"Falha ao ler cabeçalho da ROM: {str(e)}"}

def load_profile_options(title_id_hex: str) -> dict:
    default_opts = {
        "shown_only": True,
        "skip_bottom": True,
        "cull": True,
        "half_precision": True,
        "plain_math": True,
        "skip_right_eye": True,
        "frame_skip": 3
    }
    profile_file = TITLES_DIR / title_id_hex / "profile.toml"
    if profile_file.is_file():
        text = profile_file.read_text(encoding="utf-8")
        for key in default_opts.keys():
            m = re.search(rf'^{key}\s*=\s*(true|false|\d+)', text, re.MULTILINE)
            if m:
                val = m.group(1)
                if val in ("true", "false"):
                    default_opts[key] = (val == "true")
                elif val.isdigit():
                    default_opts[key] = int(val)
    return default_opts

def save_profile_file(title_id_hex: str, name: str, opts: dict):
    out_dir = TITLES_DIR / title_id_hex
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_file = out_dir / "profile.toml"
    
    lines = [
        f'name = "{name}"\n',
        '[advanced]'
    ]
    if opts.get("shown_only", True):
        lines.append('shown_only = true')
    else:
        lines.append('shown_only = false')
        
    if not opts.get("skip_bottom", True):
        lines.append('skip_bottom = false')
    if not opts.get("cull", True):
        lines.append('cull = false')
    if not opts.get("half_precision", True):
        lines.append('half_precision = false')
    if not opts.get("plain_math", True):
        lines.append('plain_math = false')
    if not opts.get("skip_right_eye", True):
        lines.append('skip_right_eye = false')
    if opts.get("frame_skip", 3) != 3:
        lines.append(f'frame_skip = {opts.get("frame_skip", 3)}')
        
    profile_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

def check_handheld(ip: str) -> dict:
    try:
        # Teste de ping rápido
        ping_res = subprocess.run(["ping", "-c", "1", "-W", "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ping_res.returncode != 0:
            return {"online": False, "ip": ip, "message": "Console inacessível ou em repouso (Ping falhou)"}
            
        # Teste de SSH
        cmd = [
            "ssh", "-o", "ConnectTimeout=2", "-o", "BatchMode=yes", f"root@{ip}",
            "df -h /storage | tail -1 | awk '{print $4}'; cat /sys/class/power_supply/*/capacity 2>/dev/null | head -1; cat /sys/devices/platform/*.gpu/devfreq/*.gpu/cur_freq 2>/dev/null"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        if res.returncode == 0:
            lines = res.stdout.strip().split("\n")
            free_storage = lines[0] if len(lines) > 0 else "N/A"
            battery = f"{lines[1]}%" if len(lines) > 1 and lines[1].strip() else "100%"
            gpu_freq = f"{int(lines[2])//1000000} MHz" if len(lines) > 2 and lines[2].isdigit() else "800 MHz"
            return {
                "online": True,
                "ip": ip,
                "free_storage": free_storage,
                "battery": battery,
                "gpu_freq": gpu_freq
            }
        else:
            return {"online": False, "ip": ip, "message": "Dispositivo respondeu ao ping mas recusou conexão SSH (root)"}
    except Exception as e:
        return {"online": False, "ip": ip, "message": str(e)}

def pick_file_dialog() -> str:
    try:
        res = subprocess.run([
            "zenity", "--file-selection",
            "--title=Selecione a ROM de Nintendo 3DS (.3ds, .cci, .cia)",
            "--file-filter=ROMs de 3DS (*.3ds, *.cci, *.cia) | *.3ds *.cci *.cia *.3DS *.CCI *.CIA"
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return ""

def run_build_thread(rom_path_str: str, title_name: str, options: dict, deploy_ip: str, do_deploy: bool):
    global build_state
    try:
        with build_lock:
            build_state["running"] = True
            build_state["progress"] = 5
            build_state["status"] = "running"
            build_state["error"] = None
            build_state["success"] = False
            build_state["logs"] = []

        add_log(f"Iniciando processo de port para: {title_name}")
        set_progress(10, "Inspecionando e validando ROM...")
        info = inspect_rom_file(rom_path_str)
        if "error" in info:
            raise RuntimeError(info["error"])
        if not info["decrypted"]:
            raise RuntimeError("Esta ROM ainda está encriptada! Decripte o arquivo pelo GodMode9 do seu 3DS antes de portar.")

        title_id = info["title_id"]
        add_log(f"ROM Validada com Sucesso: Title ID={title_id}, Produto={info['product_code']}")

        # Salvar profile
        set_progress(15, "Atualizando perfil de desempenho...")
        save_profile_file(title_id, title_name, options)

        # Encontrar docker image
        set_progress(20, "Preparando ambiente Docker...")
        img_inspect = subprocess.run(["docker", "images", "-q", "pocket-native-build:4e6f64881f12"], stdout=subprocess.PIPE, text=True)
        img_id = img_inspect.stdout.strip()
        if not img_id:
            # fallback para qualquer tag pocket-native-build
            img_inspect = subprocess.run(["docker", "images", "pocket-native-build", "--format", "{{.Repository}}:{{.Tag}}"], stdout=subprocess.PIPE, text=True)
            img_id = img_inspect.stdout.splitlines()[0].strip() if img_inspect.stdout.strip() else ""
            if not img_id:
                raise RuntimeError("Imagem Docker pocket-native-build não encontrada! Execute scripts/build.sh uma vez para criá-la.")
        else:
            img_id = "pocket-native-build:4e6f64881f12"
        add_log(f"Usando imagem Docker: {img_id}")

        set_progress(30, "Executando 3dsrecomp (recompilação estática de ARM para C)...")
        rom_path = Path(rom_path_str).resolve()
        rom_dir = rom_path.parent
        rom_file = rom_path.name

        cmd = [
            "docker", "run", "--rm",
            "-u", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{REPO_DIR}:/src:ro",
            "-v", f"{BUILD_DIR}/.work:/work",
            "-v", f"{BUILD_DIR}:/out",
            "-v", f"{rom_dir}:/rom:ro",
            "-e", "CARGO_HOME=/work/cargo",
            "-e", "CARGO_TARGET_DIR=/work/target",
            "-e", "HOME=/work",
            img_id,
            "bash", "/src/scripts/container/inside.sh", f"/rom/{rom_file}"
        ]

        add_log(f"Comando: {' '.join(cmd)}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        
        for line in proc.stdout:
            line_str = line.strip()
            if line_str:
                add_log(line_str)
                if "recompiling the game" in line_str:
                    set_progress(45, "Recompilando código do jogo para ARM64...")
                elif "Compiling" in line_str:
                    set_progress(60, "Compilando bibliotecas e motor...")
                elif "putting Pocket Native together" in line_str:
                    set_progress(75, "Empacotando estrutura para o port...")

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"A compilação falhou com código de saída {proc.returncode}. Verifique os logs.")

        add_log("Recompilação e empacotamento local concluídos!")

        if do_deploy:
            set_progress(80, f"Conectando ao RG DS ({deploy_ip}) via SCP...")
            # Checar conexão
            chk = check_handheld(deploy_ip)
            if not chk.get("online"):
                raise RuntimeError(f"Não foi possível conectar ao RG DS em {deploy_ip}. Ligue o console e verifique a rede.")

            # Copiar game folder
            game_local = BUILD_DIR / "Pocket Native" / "pocket-native" / "games" / title_id
            pn_local = BUILD_DIR / "Pocket Native" / "pocket-native"
            
            subprocess.run(["ssh", f"root@{deploy_ip}", f"mkdir -p '/storage/roms/ports/Pocket Native/pocket-native/games/{title_id}'"], check=True)
            subprocess.run(["ssh", f"root@{deploy_ip}", "ln -sfn '/storage/roms/ports/Pocket Native/pocket-native' '/storage/roms/ports/pocket-native'"], check=True)
            subprocess.run(["scp", "-r", str(game_local), f"root@{deploy_ip}:/storage/roms/ports/Pocket Native/pocket-native/games/"], check=True)

            set_progress(90, "Enviando binários e scripts do console...")
            subprocess.run(["scp", str(pn_local / "pocket-native"), f"root@{deploy_ip}:/storage/roms/ports/Pocket Native/pocket-native/"], check=True)
            subprocess.run(["scp", str(pn_local / "run.sh"), f"root@{deploy_ip}:/storage/roms/ports/Pocket Native/pocket-native/"], check=True)
            subprocess.run(["scp", str(pn_local / "power.sh"), f"root@{deploy_ip}:/storage/roms/ports/Pocket Native/pocket-native/"], check=True)
            subprocess.run(["scp", "-r", str(pn_local / "titles"), f"root@{deploy_ip}:/storage/roms/ports/Pocket Native/pocket-native/"], check=True)

            # Achar launcher .sh criado em build/Pocket Native/
            launcher_files = list((BUILD_DIR / "Pocket Native").glob("*.sh"))
            for lf in launcher_files:
                add_log(f"Enviando inicializador: {lf.name}...")
                subprocess.run(["scp", str(lf), f"root@{deploy_ip}:/storage/roms/ports/"], check=True)

            # Ajustar permissões
            subprocess.run(["ssh", f"root@{deploy_ip}", "chmod +x /storage/roms/ports/*.sh '/storage/roms/ports/Pocket Native/pocket-native/'* 2>/dev/null"], check=True)

            set_progress(95, "Atualizando foco no EmulationStation...")
            subprocess.run([
                "ssh", f"root@{deploy_ip}",
                "export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock; swaymsg '[app_id=\"emulationstation\"] focus' 2>/dev/null || true"
            ])

        set_progress(100, "Concluído com Sucesso!")
        add_log("=== PORT FINALIZADO COM SUCESSO! ===")
        if do_deploy:
            add_log(f"O jogo '{title_name}' já está instalado e disponível no menu Ports do seu Anbernic RG DS!")
        else:
            add_log("Arquivos gerados prontos em build/Pocket Native/")

        with build_lock:
            build_state["running"] = False
            build_state["status"] = "success"
            build_state["success"] = True

    except Exception as e:
        err_msg = str(e)
        add_log(f"ERRO: {err_msg}")
        with build_lock:
            build_state["running"] = False
            build_state["status"] = "error"
            build_state["error"] = err_msg

class StudioHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPO_DIR / "gui"), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/status":
            query = urllib.parse.parse_qs(parsed.query)
            ip = query.get("ip", [DEFAULT_IP])[0]
            status = check_handheld(ip)
            self._send_json(status)
        elif parsed.path == "/api/build_state":
            with build_lock:
                state_copy = dict(build_state)
            self._send_json(state_copy)
        elif parsed.path == "/api/list_games":
            query = urllib.parse.parse_qs(parsed.query)
            ip = query.get("ip", [DEFAULT_IP])[0]
            games = []
            try:
                cmd = ["ssh", "-o", "ConnectTimeout=2", f"root@{ip}", "ls -1 '/storage/roms/ports/Pocket Native/pocket-native/games' 2>/dev/null"]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, timeout=3)
                if res.returncode == 0:
                    for gid in res.stdout.strip().splitlines():
                        if gid.strip():
                            # Ler o profile local
                            opts = load_profile_options(gid)
                            pfile = TITLES_DIR / gid / "profile.toml"
                            name = gid
                            if pfile.is_file():
                                m = re.search(r'name\s*=\s*"([^"]+)"', pfile.read_text(encoding="utf-8"))
                                if m: name = m.group(1)
                            games.append({"id": gid, "name": name, "options": opts})
            except Exception:
                pass
            self._send_json({"games": games})
        elif parsed.path == "/api/game_log":
            query = urllib.parse.parse_qs(parsed.query)
            ip = query.get("ip", [DEFAULT_IP])[0]
            gid = query.get("id", ["0004000000053F00"])[0]
            log_content = ""
            try:
                cmd = ["ssh", "-o", "ConnectTimeout=2", f"root@{ip}", f"cat '/storage/roms/ports/Pocket Native/pocket-native/games/{gid}/data/log.txt' 2>/dev/null | tail -100"]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, text=True, timeout=3)
                if res.returncode == 0:
                    log_content = res.stdout
            except Exception as e:
                log_content = f"Erro ao obter log: {str(e)}"
            self._send_json({"log": log_content})
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        data = {}
        if post_data:
            try:
                data = json.loads(post_data.decode("utf-8"))
            except Exception:
                pass

        if parsed.path == "/api/pick_file":
            selected = pick_file_dialog()
            self._send_json({"path": selected})
        elif parsed.path == "/api/inspect":
            rom_path = data.get("path", "")
            result = inspect_rom_file(rom_path)
            self._send_json(result)
        elif parsed.path == "/api/build":
            if build_state["running"]:
                self._send_json({"error": "Já existe uma compilação em andamento."})
                return
            rom_path = data.get("rom_path", "")
            title_name = data.get("name", "Jogo 3DS")
            options = data.get("options", {})
            deploy_ip = data.get("ip", DEFAULT_IP)
            do_deploy = data.get("deploy", True)
            
            thread = threading.Thread(
                target=run_build_thread,
                args=(rom_path, title_name, options, deploy_ip, do_deploy),
                daemon=True
            )
            thread.start()
            self._send_json({"started": True})
        elif parsed.path == "/api/remote_action":
            action = data.get("action", "")
            ip = data.get("ip", DEFAULT_IP)
            result = {"success": True}
            try:
                if action == "focus_es":
                    subprocess.run(["ssh", f"root@{ip}", "export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock; swaymsg '[app_id=\"emulationstation\"] focus'"], timeout=3)
                elif action == "kill_game":
                    subprocess.run(["ssh", f"root@{ip}", "pkill -9 pocket-native"], timeout=3)
                elif action == "performance":
                    subprocess.run(["ssh", f"root@{ip}", "'/storage/roms/ports/Pocket Native/pocket-native/power.sh' performance"], timeout=3)
            except Exception as e:
                result = {"success": False, "error": str(e)}
            self._send_json(result)
        else:
            self.send_error(404, "Endpoint não encontrado")

    def _send_json(self, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

def find_available_port(start_port=8383):
    port = start_port
    while port < start_port + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    return start_port

def main():
    port = find_available_port(8383)
    server_address = ("127.0.0.1", port)
    httpd = http.server.ThreadingHTTPServer(server_address, StudioHTTPHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"\n==================================================")
    print(f"  NativeRG3DS Studio rodando em: {url}")
    print(f"==================================================\n")
    
    # Abrir navegador automaticamente
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor finalizado.")
        httpd.server_close()

if __name__ == "__main__":
    main()
