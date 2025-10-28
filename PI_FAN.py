import socket
import pigpio
import sys
import signal

# =============================
# 설정
# =============================
PWM_PIN = 21        # BCM 기준, 고정
FREQ_HZ = 25000     # 25kHz (팬에 적합)
HOST = "0.0.0.0"
PORT = 7000

# =============================
# pigpio 초기화
# =============================
pi = pigpio.pi()
if not pi.connected:
    print("[PI] pigpio 데몬 연결 실패. 'sudo systemctl start pigpiod' 확인하세요.", file=sys.stderr)
    sys.exit(1)

pi.set_mode(PWM_PIN, pigpio.OUTPUT)
pi.set_PWM_frequency(PWM_PIN, FREQ_HZ)
pi.set_PWM_range(PWM_PIN, 255)
pi.set_PWM_dutycycle(PWM_PIN, 0)

# =============================
# 안전 종료 핸들러
# =============================
def cleanup(*_):
    pi.set_PWM_dutycycle(PWM_PIN, 0)
    pi.stop()
    print("\n[PI] GPIO21 PWM 종료 및 정리 완료.")
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

# =============================
# TCP 서버
# =============================
print(f"[PI] Fan controller (GPIO21, {FREQ_HZ}Hz) listening on {HOST}:{PORT}")

try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(1)

        while True:
            conn, addr = server.accept()
            print(f"[PI] Connected from {addr}")

            with conn, conn.makefile("r") as rf:
                for line in rf:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        val = int(line)
                        val = max(0, min(255, val))
                        pi.set_PWM_dutycycle(PWM_PIN, val)
                        print(f"[PI] PWM={val} (Duty={val/255*100:.1f}%)")
                    except ValueError:
                        print(f"[PI] Invalid data received: {line}")
                    except Exception as e:
                        print(f"[PI] Error: {e}")
                        pi.set_PWM_dutycycle(PWM_PIN, 0)
except KeyboardInterrupt:
    cleanup()
except Exception as e:
    print(f"[PI] Fatal error: {e}", file=sys.stderr)
    cleanup()
