import os
import uuid
import subprocess
import urllib.request
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

# --- КОНФИГУРАЦИЯ ---
ENV_SERVER_URL = os.getenv("SERVER_URL") or os.getenv("SERVER_IP")
SERVER_PORT = int(os.getenv("SERVERPORT", "51820"))
VPN_SUBNET = os.getenv("INTERNAL_SUBNET", "10.13.13.0")
PEER_DNS = os.getenv("PEERDNS", "1.1.1.1")

CONF_DIR = "/etc/amnezia/amneziawg" # Исправленный путь
CONF_FILE = f"{CONF_DIR}/wg0.conf"
PRIVATE_KEY_FILE = f"{CONF_DIR}/private.key"
PUBLIC_KEY_FILE = f"{CONF_DIR}/public.key"

# Параметры обфускации (слитно)
OBFUSCATION_PARAMS = (
    "Jc = 4\n"
    "Jmin = 40\n"
    "Jmax = 70\n"
    "S1 = 0\n"
    "S2 = 0\n"
    "H1 = 1\n"
    "H2 = 2\n"
    "H3 = 3\n"
    "H4 = 4"
)

if not os.path.exists(CONF_DIR):
    os.makedirs(CONF_DIR, exist_ok=True)

class PeerCreate(BaseModel):
    name: str

def run_cmd(cmd):
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running: {cmd}\n{e}")

def get_public_ip():
    try:
        return urllib.request.urlopen('https://ifconfig.me/ip').read().decode('utf8').strip()
    except Exception:
        return "127.0.0.1"

if ENV_SERVER_URL and ENV_SERVER_URL != "0.0.0.0":
    FINAL_SERVER_IP = ENV_SERVER_URL
else:
    FINAL_SERVER_IP = get_public_ip()

def setup_network():
    print("🔧 Configuring AmneziaWG Interface...")
    if not os.path.exists(PRIVATE_KEY_FILE):
        print("🔑 Generating server keys...")
        priv = subprocess.check_output(["wg", "genkey"]).decode().strip()
        with open(PRIVATE_KEY_FILE, "w") as f: f.write(priv)
        proc = subprocess.Popen(["wg", "pubkey"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        pub, _ = proc.communicate(input=priv.encode())
        with open(PUBLIC_KEY_FILE, "w") as f: f.write(pub.decode().strip())

    run_cmd("ip link delete wg0 2>/dev/null || true")
    # Используем стандартный wireguard-go (он пропатчен в этом образе)
    subprocess.Popen(["wireguard-go", "wg0"])
    
    import time
    time.sleep(1)

    server_ip_cidr = f"{VPN_SUBNET.rsplit('.', 1)[0]}.1/24"
    run_cmd(f"ip address add {server_ip_cidr} dev wg0")
    
    with open(PRIVATE_KEY_FILE, "r") as f: priv_key = f.read().strip()
    
    temp_conf = f"/tmp/wg0_init.conf"
    with open(temp_conf, "w") as f:
        f.write(f"[Interface]\nPrivateKey = {priv_key}\nListenPort = {SERVER_PORT}\n{OBFUSCATION_PARAMS}")
    
    run_cmd(f"wg setconf wg0 {temp_conf}")
    run_cmd("ip link set mtu 1280 up dev wg0")

    # Firewall
    run_cmd("sysctl -w net.ipv4.ip_forward=1")
    run_cmd("iptables -t nat -F")
    run_cmd("iptables -F")
    run_cmd("iptables -P FORWARD ACCEPT")
    run_cmd("iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE")
    run_cmd("iptables -A FORWARD -i wg0 -j ACCEPT")
    run_cmd("iptables -A FORWARD -o wg0 -j ACCEPT")

    restore_peers()

def restore_peers():
    if not os.path.exists(CONF_FILE): return
    try:
        run_cmd(f"wg addconf wg0 {CONF_FILE}")
        print("✅ Peers restored.")
    except Exception:
        pass

setup_network()

def get_server_pubkey():
    if os.path.exists(PUBLIC_KEY_FILE):
        with open(PUBLIC_KEY_FILE, "r") as f: return f.read().strip()
    return "UNKNOWN"

def get_next_ip():
    used_ips = set()
    used_ips.add("1")
    try:
        # Парсим используемые IP прямо из конфига (надежнее)
        if os.path.exists(CONF_FILE):
            with open(CONF_FILE, "r") as f:
                content = f.read()
                # Ищем AllowedIPs = 10.13.13.X/32
                ips = re.findall(r"AllowedIPs\s*=\s*[\d\.]+\.(\d+)/32", content)
                used_ips.update(ips)
    except: pass

    for i in range(2, 255):
        if str(i) not in used_ips:
            base = VPN_SUBNET.rsplit('.', 1)[0]
            return f"{base}.{i}"
    raise Exception("IP Limit Reached")

# --- API ENDPOINTS ---

@app.get("/api/status")
def status():
    try:
        output = subprocess.check_output(["wg", "show", "wg0"]).decode()
        peers_count = output.count("peer:")
        return {"peers_count": peers_count, "status": "ok"}
    except Exception:
        return {"peers_count": 0, "status": "error"}

@app.post("/api/reload")
def reload_vpn():
    try:
        setup_network()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/peers")
def create_peer(req: PeerCreate):
    try:
        priv_key = subprocess.check_output(["wg", "genkey"]).decode().strip()
        proc = subprocess.Popen(["wg", "pubkey"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        pub_key, _ = proc.communicate(input=priv_key.encode())
        pub_key = pub_key.decode().strip()
        
        server_pub = get_server_pubkey()
        client_ip = get_next_ip()
        uid = str(uuid.uuid4())

        # Конфиг клиента (монолитный)
        config_content = f"""[Interface]
PrivateKey = {priv_key}
Address = {client_ip}/32
DNS = {PEER_DNS}
MTU = 1280
{OBFUSCATION_PARAMS}

[Peer]
PublicKey = {server_pub}
Endpoint = {FINAL_SERVER_IP}:{SERVER_PORT}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25"""

        # Блок для конфига сервера
        peer_block = f"\n[Peer]\n# Name = {req.name}\n# UUID = {uid}\nPublicKey = {pub_key}\nAllowedIPs = {client_ip}/32\n"
        
        with open(CONF_FILE, "a") as f: f.write(peer_block)
        run_cmd(f"wg set wg0 peer {pub_key} allowed-ips {client_ip}/32")

        return {"uid": uid, "config": config_content, "client_ip": client_ip}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/peers/{uid}")
def delete_peer(uid: str):
    try:
        if not os.path.exists(CONF_FILE):
            raise HTTPException(status_code=404, detail="Config not found")

        with open(CONF_FILE, "r") as f:
            content = f.read()

        # Ищем блок пира по UUID. 
        # Блок начинается с [Peer] и содержит наш UUID в комментарии.
        # Регулярка ищет от [Peer] до следующего [Peer] или конца файла.
        pattern = re.compile(r"(\[Peer\]\s*# Name = .*?# UUID = " + re.escape(uid) + r".*?)(?=\n\[Peer\]|$)", re.DOTALL)
        match = pattern.search(content)

        if not match:
            raise HTTPException(status_code=404, detail="Peer not found")

        peer_block = match.group(1)
        
        # Извлекаем PublicKey, чтобы удалить из рантайма
        pub_match = re.search(r"PublicKey\s*=\s*(.*)", peer_block)
        if pub_match:
            pub_key = pub_match.group(1).strip()
            run_cmd(f"wg set wg0 peer {pub_key} remove")
        
        # Удаляем блок из текста конфига
        new_content = content.replace(peer_block, "")
        
        # Убираем лишние пустые строки (косметика)
        new_content = re.sub(r'\n\s*\n', '\n', new_content)
        
        with open(CONF_FILE, "w") as f:
            f.write(new_content)

        return {"status": "deleted"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))