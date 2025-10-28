import os, socket, json, random, time
from contextlib import closing

PI_VPN_IP   = os.getenv("PI_VPN_IP", "192.168.43.4")  # 예시: VPN이 준 Pi의 IP
PI_VPN_PORT = int(os.getenv("PI_VPN_PORT", "7000"))
PERIOD_SEC  = float(os.getenv("PERIOD_SEC", "5"))

def send_once(pwm):
    with closing(socket.create_connection((PI_VPN_IP, PI_VPN_PORT), timeout=5)) as s:
        msg = json.dumps({"pwm": pwm}) + "\n"
        s.sendall(msg.encode("utf-8"))

def main():
    print(f"[GCP->Pi] target={PI_VPN_IP}:{PI_VPN_PORT}, every {PERIOD_SEC}s")
    while True:
        pwm = random.randint(0, 100)
        try:
            send_once(pwm)
            print(f"[SEND] pwm={pwm}%")
        except Exception as e:
            print(f"[SEND] fail: {e}")
        time.sleep(PERIOD_SEC)

if __name__ == "__main__":
    main()