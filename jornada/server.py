import json
import hashlib
import mimetypes
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
SUBSCRIBERS = set()
SEQUENCE = 0
STARTED_AT = time.time_ns()

BIDS = [
    {"bidId": "seed-1", "bidder": "#A8841", "amount": 128500, "sequence": 1, "occurredAtNs": STARTED_AT - 5000000000},
    {"bidId": "seed-2", "bidder": "#B1029", "amount": 127000, "sequence": 2, "occurredAtNs": STARTED_AT - 4300000000},
    {"bidId": "seed-3", "bidder": "#C6712", "amount": 125500, "sequence": 3, "occurredAtNs": STARTED_AT - 3800000000},
    {"bidId": "seed-4", "bidder": "#A8841", "amount": 124000, "sequence": 4, "occurredAtNs": STARTED_AT - 3100000000},
    {"bidId": "seed-5", "bidder": "#D3308", "amount": 122500, "sequence": 5, "occurredAtNs": STARTED_AT - 2400000000},
]
SEQUENCE = 5
IDEMPOTENCY = {}
RESERVE = 98000
USERS_FILE = ROOT / "users.json"


def load_users():
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


USERS = load_users()


def password_hash(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def validate_cpf(cpf):
    digits = "".join(char for char in cpf if char.isdigit())
    if len(digits) != 11:
        return False
    return True


def ordered_bids():
    return sorted(BIDS, key=lambda bid: (-bid["amount"], bid["occurredAtNs"], bid["sequence"]))


def fraud_score(bidder):
    recent = [bid for bid in ordered_bids() if bid["bidder"] == bidder][-8:]
    if len(recent) >= 5:
        return 42
    if len(recent) >= 3:
        return 18 + len(recent)
    return 8 + len(recent)


def state():
    bids = ordered_bids()
    bidder = bids[0]["bidder"] if bids else None
    score = fraud_score(bidder) if bidder else 8
    return {
        "auctionId": "2847",
        "bids": bids[:20],
        "winner": bids[0] if bids else None,
        "increment": 500,
        "risk": {
            "score": score,
            "level": "ALTO" if score >= 35 else "BAIXO",
            "message": "Revisao de conta ativada" if score >= 35 else "Nenhuma anomalia critica",
        },
        "latencyP95Ms": 18,
        "bidsPerMinute": 146,
        "reserve": RESERVE,
    }


def publish(event):
    payload = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
    with LOCK:
        subscribers = list(SUBSCRIBERS)
    for subscriber in subscribers:
        try:
            subscriber(payload)
        except Exception:
            with LOCK:
                SUBSCRIBERS.discard(subscriber)


class AuctionHandler(BaseHTTPRequestHandler):
    server_version = "AuctionLocal/1.0"

    def log_message(self, format, *args):
        print(f"[server] {self.address_string()} - {format % args}")

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/health":
            self.send_json(200, {"status": "ok", "auctionId": "2847"})
            return

        if path == "/api/state":
            self.send_json(200, state())
            return

        if path == "/api/session":
            authorization = self.headers.get("Authorization", "")
            if authorization.startswith("Bearer auction-"):
                self.send_json(200, {"authenticated": True})
            else:
                self.send_json(401, {"authenticated": False})
            return

        if path == "/api/events":
            self.send_events()
            return

        if path == "/" or path == "/index.html":
            self.serve_file(ROOT / "index.html", "text/html")
            return

        target = (ROOT / path.lstrip("/")).resolve()
        if ROOT in target.parents and target.is_file():
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.serve_file(target, content_type)
            return

        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/register":
            try:
                payload = self.read_json()
                name = str(payload.get("name", "")).strip()
                cpf = str(payload.get("cpf", "")).strip()
                email = str(payload.get("email", "")).strip().lower()
                password = str(payload.get("password", "")).strip()
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"authenticated": False, "error": "Dados invalidos"})
                return

            cpf_digits = "".join(char for char in cpf if char.isdigit())
            if len(name) < 3:
                self.send_json(400, {"authenticated": False, "error": "Informe seu nome completo"})
                return
            if not validate_cpf(cpf):
                self.send_json(400, {"authenticated": False, "error": "CPF invalido"})
                return
            if "@" not in email or "." not in email:
                self.send_json(400, {"authenticated": False, "error": "E-mail invalido"})
                return
            if len(password) < 4:
                self.send_json(400, {"authenticated": False, "error": "A senha deve ter 4 caracteres"})
                return
            if email in USERS:
                self.send_json(409, {"authenticated": False, "error": "Este e-mail ja esta cadastrado"})
                return
            if any(user["cpf"] == cpf_digits for user in USERS.values()):
                self.send_json(409, {"authenticated": False, "error": "Este CPF ja esta cadastrado"})
                return

            bidder = "#" + uuid.uuid4().hex[:6].upper()
            user = {
                "name": name,
                "cpf": cpf_digits,
                "email": email,
                "bidder": bidder,
                "passwordHash": password_hash(password),
            }
            USERS[email] = user
            USERS_FILE.write_text(json.dumps(USERS, ensure_ascii=False, indent=2), encoding="utf-8")
            token = "auction-" + uuid.uuid4().hex
            self.send_json(201, {
                "authenticated": True,
                "token": token,
                "user": {"name": name, "email": email, "cpf": cpf_digits, "bidder": bidder},
            })
            return

        if path == "/api/login":
            try:
                payload = self.read_json()
                email = str(payload.get("email", "")).strip().lower()
                password = str(payload.get("password", "")).strip()
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "Credenciais invalidas"})
                return

            user = USERS.get(email)
            if not user or user["passwordHash"] != password_hash(password):
                self.send_json(401, {"authenticated": False, "error": "E-mail ou senha invalidos"})
                return
            name = user["name"]
            bidder = user["bidder"]
            self.send_json(200, {
                "authenticated": True,
                "token": "auction-" + uuid.uuid4().hex,
                "user": {
                    "name": name,
                    "email": email,
                    "cpf": user["cpf"],
                    "bidder": bidder,
                },
            })
            return

        if path != "/api/bids":
            self.send_json(404, {"error": "Not found"})
            return

        try:
            payload = self.read_json()
            amount = int(payload.get("amount", 0))
            bidder = str(payload.get("bidder", "#YOU-01")).strip()[:24] or "#YOU-01"
            bid_id = self.headers.get("Idempotency-Key") or str(payload.get("bidId") or uuid.uuid4())
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"error": "Payload invalido"})
            return

        global SEQUENCE
        with LOCK:
            if bid_id in IDEMPOTENCY:
                existing = IDEMPOTENCY[bid_id]
                self.send_json(200, {"accepted": True, "duplicate": True, "bid": existing, "state": state()})
                return

            winner = ordered_bids()[0] if BIDS else None
            minimum = (winner["amount"] if winner else RESERVE) + 500
            if amount < minimum:
                self.send_json(409, {
                    "accepted": False,
                    "error": f"Lance inferior ao minimo de R$ {minimum}",
                    "minimum": minimum,
                    "state": state(),
                })
                return

            SEQUENCE += 1
            bid = {
                "bidId": bid_id,
                "bidder": bidder,
                "amount": amount,
                "sequence": SEQUENCE,
                "occurredAtNs": time.time_ns(),
            }
            BIDS.append(bid)
            IDEMPOTENCY[bid_id] = bid
            snapshot = state()

        publish({"type": "BID_ACCEPTED", "bid": bid, "state": snapshot})
        self.send_json(201, {"accepted": True, "duplicate": False, "bid": bid, "state": snapshot})

    def serve_file(self, target, content_type):
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def send(payload):
            try:
                self.wfile.write(payload)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                raise

        with LOCK:
            SUBSCRIBERS.add(send)

        try:
            send(f"data: {json.dumps({'type': 'CONNECTED', 'state': state()}, ensure_ascii=False)}\n\n".encode())
            while True:
                time.sleep(15)
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            with LOCK:
                SUBSCRIBERS.discard(send)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 4173), AuctionHandler)
    print("Auction backend running at http://localhost:4173")
    print("Health: http://localhost:4173/api/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
