import socket 
import json
import random
import time

# 임의 전송 client 코드
PI_HOST = '127.0.0.1'
PI_PORT = '6000'

def send_to_pi(pwm_value: int):
    payload = json.dumps({"pwm": int(pwm_value)}).encode() # JSON 포맷으로 변경
    try:
        with socket.create_connection((PI_HOST, PI_PORT), timeout=2) as s:
            s.sendall(payload)
    except Exception as e:
        print(f"[Network] Pi 전송 실패: {e}")

def main():
    while True:
        send_to_pi(random.randint(0,100))
        time.sleep(5)

main()
