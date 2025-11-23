import socket
import threading
import json
import time
import requests

# --- 설정 (사용자 환경에 맞게 수정) ---

# InfluxDB 2.x 기준
INFLUXDB_URL="http://localhost:8086/api/v2/write?org=ORG&bucket=BUCKET&precision=s"
INFLUXDB_TOKEN = "TOKEN" 

headers = {
    "Authorization": f"Token {INFLUXDB_TOKEN}",
    "Content-Type": "text/plain; charset=utf-8"
}

# 데이터 식별을 위한 태그 (Tag) 설정
DEVICE_ID = "raspberrypi-fan-01"

# 제어 서버로부터 명령을 수신할 포트 [VPN]
CONTROL_SERVER_HOST = '0.0.0.0' 
CONTROL_SERVER_PORT = 6000       

# --- 시뮬레이션 모드 설정 ---.
SIMULATION_MODE = False

if not SIMULATION_MODE:
    try:
        import RPi.GPIO as GPIO
        FAN_PIN = 18  # 임의의 GPIO PIN
        PWM_FREQUENCY = 100 # PWM 주파수 (0~100)
    except (ImportError, RuntimeError):
        print("[오류] RPi.GPIO 라이브러리를 찾을 수 없습니다. 시뮬레이션 모드로 전환합니다.")
        SIMULATION_MODE = True

# --- 전역 변수 및 동기화 ---

# 현재 PWM 값을 저장할 변수 (여러 스레드에서 접근)
# 초기값은 0 (팬 정지)
current_pwm_value = 0

# 스레드 간의 안전한 데이터 공유를 위한 Lock
lock = threading.Lock()

# --- 코드 본문 ---

def setup_gpio():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(FAN_PIN, GPIO.OUT)
    fan = GPIO.PWM(FAN_PIN, PWM_FREQUENCY)
    fan.start(0)
    print(f"[GPIO] 핀 {FAN_PIN}을 PWM 모드로 설정했습니다.")
    return fan

def set_fan_speed(pwm_value, fan_controller):
    global current_pwm_value # 전역변수 사용
    
    pwm_value = max(0, min(100, pwm_value)) # 0~100

    with lock:
        current_pwm_value = pwm_value
    
    if not SIMULATION_MODE and fan_controller:
        fan_controller.ChangeDutyCycle(current_pwm_value)
    
    print(f"[제어] 팬 PWM이 {current_pwm_value}%로 설정되었습니다.")

# 제어서버로 부터 pwm 제어신호 수신
def handle_control_client(conn, addr, fan_controller):
    print(f"[제어 서버] 연결됨: {addr}")
    try:
        with conn, conn.makefile('r') as rf:
            for line in rf:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    # JSON 데이터 파싱
                    data = json.loads(line)
                    new_pwm = data.get('pwm')
                    
                    if new_pwm is not None and isinstance(new_pwm, int):
                        set_fan_speed(new_pwm, fan_controller)
                    else:
                        print(f"[제어 서버] 잘못된 데이터 수신: {line}")
                        
                except json.JSONDecodeError:
                    print(f"[제어 서버] JSON 파싱 실패: {line}")
    except Exception as e:
        print(f"[제어 서버] 클라이언트 처리 중 오류: {e}")
    finally:
        print(f"[제어 서버] 연결 종료: {addr}")

# DB 서버 대기 후 전송
def start_control_server(fan_controller):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind((CONTROL_SERVER_HOST, CONTROL_SERVER_PORT))
        server_socket.listen()
        print(f"[제어 서버] {CONTROL_SERVER_HOST}:{CONTROL_SERVER_PORT}에서 제어 명령 대기 중...")
        
        while True:
            conn, addr = server_socket.accept()
            # 각 연결을 별도의 스레드로 처리
            threading.Thread(target=handle_control_client, args=(conn, addr, fan_controller), daemon=True).start()

def report_to_influxdb():
    """1초마다 현재 PWM 값을 InfluxDB로 전송하는 함수"""
    while True:
        with lock:
            pwm_to_report = current_pwm_value
        
        # InfluxDB Line Protocol 페이로드 생성
        payload = f"fan_status,device={DEVICE_ID} pwm_duty_cycle={pwm_to_report}"
        
        try:
            response = requests.post(
                INFLUXDB_URL, 
                headers=headers, 
                data=payload.encode('utf-8'), 
                timeout=3 # 3초간 기다림
            )
            
            if response.status_code != 204:
                print(f"[DB 전송] 실패 (HTTP {response.status_code}): {response.text}")
            else:
                print(f"[DB 전송] 성공: PWM={pwm_to_report}%") # 성공 로그
                pass

        except requests.exceptions.RequestException as e:
            print(f"[DB 전송] 오류: {e}")

        # 3초 대기
        time.sleep(3)

def main():
    fan_controller = None
    if not SIMULATION_MODE:
        fan_controller = setup_gpio()

    # 1. 제어 명령을 수신하는 서버 스레드 시작
    control_thread = threading.Thread(target=start_control_server, args=(fan_controller,), daemon=True)
    control_thread.start()
    
    # 2. InfluxDB로 상태를 보고하는 스레드 시작
    report_thread = threading.Thread(target=report_to_influxdb, daemon=True)
    report_thread.start()
    
    print("[메인] 초기화 완료. 제어 및 보고 스레드 시작됨.")
    
    # 메인 스레드는 스레드들이 종료되지 않도록 대기
    try:
        while True:
            time.sleep(3600) # 메인 스레드는 할 일이 없으므로 길게 대기
    except KeyboardInterrupt:
        print("\n[종료] 프로그램을 종료합니다.")
    finally:
        if not SIMULATION_MODE and fan_controller:
            fan_controller.stop()
            GPIO.cleanup()
            print("[GPIO] GPIO 리소스를 정리했습니다.")

if __name__ == '__main__':
    main()
