import socket
import RPi.GPIO as GPIO

# =============================
# GPIO 설정
# =============================
PWM_PIN = 21           # 팬 제어용 핀 (BCM 기준)
FREQ_HZ = 25000        # PWM 주파수 (25kHz 권장, 팬에 맞게 조정)

GPIO.setmode(GPIO.BCM)
GPIO.setup(PWM_PIN, GPIO.OUT)
pwm = GPIO.PWM(PWM_PIN, FREQ_HZ)
pwm.start(0)  # 시작 시 0% Duty

# =============================
# TCP 서버 설정
# =============================
HOST = "0.0.0.0"
PORT = 7000

print(f"[PI] Fan controller listening on {HOST}:{PORT}")

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
                        # 문자열 → 정수 (0~255)
                        val = int(line)
                        val = max(0, min(255, val))

                        # 0~255 → 0~100%로 변환
                        duty = val * 100.0 / 255.0
                        pwm.ChangeDutyCycle(duty)

                        print(f"[PI] PWM={val} (Duty={duty:.1f}%)")

                    except ValueError:
                        print(f"[PI] Invalid data received: {line}")
                    except Exception as e:
                        print(f"[PI] Error: {e}")
                        pwm.ChangeDutyCycle(0)

except KeyboardInterrupt:
    print("\n[PI] Interrupted by user. Stopping fan...")
finally:
    pwm.ChangeDutyCycle(0)
    pwm.stop()
    GPIO.cleanup()
    print("[PI] GPIO cleaned up. Bye!")
