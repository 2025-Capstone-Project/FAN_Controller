import asyncio
import websockets
import json
from FANCONTROLL_PY import FanController, read_latest_values, send_to_pi

from websockets import http11

class PatchedRequest(http11.Request):
    @classmethod
    def parse(cls, read_line):
        """
        원래 http11.Request.parse() 구현에서
        'Content-Length' 체크만 제거한 버전.
        제너레이터 기반 코루틴이어야 하므로 async def 쓰면 안 됨.
        """
        # 요청 라인 파싱
        request_line = yield from read_line(http11.MAX_REQUEST_LINE)

        try:
            method, raw_path, version = request_line.split(b" ", 2)
        except ValueError:  # pragma: no cover
            raise ValueError(f"invalid HTTP request line: {request_line!r}")

        if version != b"HTTP/1.1":
            raise ValueError(f"unsupported HTTP version: {version!r}")

        if method != b"GET":
            raise ValueError(f"unsupported HTTP method: {method!r}")

        path = raw_path.decode("ascii", "surrogateescape")

        # 헤더 파싱
        headers = yield from http11.parse_headers(read_line)

        # 원래 코드:
        #   if "Transfer-Encoding" in headers: ...
        #   if "Content-Length" in headers: raise ValueError("unsupported request body")
        #
        # 우리는 'Transfer-Encoding'만 막고,
        # 'Content-Length'는 허용해야 자바 HttpClient(WebSocket)의
        # "Content-Length: 0" 헤더를 받아들일 수 있음.

        if "Transfer-Encoding" in headers:
            raise NotImplementedError("transfer codings aren't supported")

        # Content-Length는 더 이상 체크하지 않음
        return cls(path, headers)

# 실제로 websockets 내부에서 사용하는 Request 클래스를 교체
http11.Request = PatchedRequest

from FANCONTROLL_PY import FanController, read_latest_values, send_to_pi


global_ctl = FanController()

async def automation_loop():
    """
    기존 FANCONTROLL_PY.py의 main()에 있던 역할을 여기서 수행합니다.
    웹소켓 통신과 상관없이 1초마다 계속 돕니다.
    """
    print("[System] 자동 제어 루프 시작")
    while True:
        try:
            # 1. 센서 값 읽기
            vals = read_latest_values()
            cpu = vals.get("cpu_temperature", 0)
            gpu = vals.get("gpu_temperature", 0)
            model = vals.get("model_result", 0)
            
            if cpu is None: cpu = 0
            if gpu is None: gpu = 0
            if model is None: model = 0

            # 2. PWM 계산 (global_ctl의 현재 모드(auto/manual)에 따라 내부에서 계산)
            pwm_value = global_ctl.step(cpu, gpu, int(model))
            
            # 3. 라즈베리파이로 전송
            # (send_to_pi도 동기 함수이므로 짧게 실행됨)
            send_to_pi(pwm_value)
            
            # 로그 출력 (옵션)
            print(f"[Loop] Mode={global_ctl.mode}, PWM={pwm_value}, CPU={cpu}")

        except Exception as e:
            print(f"[Loop Error] {e}")

        # 4. 1초 대기 (다른 작업들에게 양보)
        await asyncio.sleep(1.0)

async def handle_connection(websocket, path):
    """웹 클라이언트 연결 처리"""
    print(f"[Web] Client connected: {websocket.remote_address}")
    try:
        async for message in websocket:
            data = json.loads(message)
            print(f"[Web] Received: {data}")

            # 1. 웹에서 온 명령을 'global_ctl'에 반영
            if "mode" in data:
                m = str(data["mode"]).lower()
                if m in ("auto", "manual", "range"):
                    global_ctl.mode = m
            
            if "manual_pwm" in data:
                global_ctl.manual_target = int(data["manual_pwm"])
            
            if "cpu_threshold" in data:
                global_ctl.cpu_thresh = int(data["cpu_threshold"])

            if "gpu_threshold" in data:
                global_ctl.gpu_thresh = int(data["gpu_threshold"])

            print(f"{global_ctl.mode}, {global_ctl.manual_target}, {global_ctl.cpu_thresh}, {global_ctl.gpu_thresh}")
            
            # 2. 현재 상태를 바로 응답 (옵션)
            response = {
                "status": "ok",
                "current_mode": global_ctl.mode,
                "current_pwm": global_ctl.last_pwm
            }
            await websocket.send(json.dumps(response))
            
    except websockets.exceptions.ConnectionClosed:
        print("[Web] Client disconnected")
async def main():
    # Web과 8765포트로 연결
    async with websockets.serve(handle_connection, "0.0.0.0", 8765):
        print("WebSocket server started at ws://0.0.0.0:8765")
        asyncio.create_task(automation_loop())
        await asyncio.Future()  # 서버가 종료되지 않도록 대기

if __name__ == "__main__":

    asyncio.run(main())








