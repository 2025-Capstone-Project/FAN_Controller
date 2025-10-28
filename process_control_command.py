import asyncio
import websockets
import json
from FANCONTROLL_PY import FanController, read_latest_values

# 이 핸들러는 클라이언트 한 명과 연결이 유지되는 동안 계속 실행됩니다.
async def handle_connection(websocket, path):
    print(f"Client connected from {websocket.remote_address}")
    # 팬 컨트롤러 객체를 연결마다 하나씩 생성
    # 모드는 첫 메시지로 설정하거나, 기본값으로 시작할 수 있습니다.
    ctl = FanController(mode="auto") 

    try:
        # async for를 사용해 클라이언트가 보내는 모든 메시지를 계속해서 받습니다.
        async for message in websocket:
            data = json.loads(message)
            print(f"Received data: {data}")

            # 받은 데이터 처리
            mode = data.get("mode")
            if mode:
                ctl.set_mode(mode) # FanController에 모드 변경 메서드가 있다고 가정

            manual_pwm = data.get("manual_pwm")
            cpu_threshold = data.get("cpu_threshold", 40)
            gpu_threshold = data.get("gpu_threshold", 40)

            """
            current_cpu_temp = 35 # 예시: 실제 센서 값 읽어오기
            current_gpu_temp = 42 # 예시: 실제 센서 값 읽어오기
            """
            try:
                    vals= read_latest_values()

                    cpu_temp= vals.get("cpu_temperature")
                    gpu_temp= vals.get("gpu_temperature")
                    model_result= vals.get("model_result")

                    if cpu_temp is None or gpu_temp is None or model_result is None:
                        raise ValueError("InfluxDB에서 못가져왔어요!")
            except Exception as e:
                    print(f"센서 데이터 읽기 오류: {e}")
                    await websocket.send(json.dumps({"status": "error", "message": str(e)}))
                    continue
            
            pwm_value = ctl.step(
                cpu_temp=cpu_temp, 
                gpu_temp=gpu_temp, 
                model_result=int(model_result), 
                manual_pwm=manual_pwm, 
                cpu_threshold=cpu_threshold, 
                gpu_threshold=gpu_threshold
            )

            # 결과를 프론트로 전송
            result_data = {"status": "success", "pwm": pwm_value, "cpu_temp": current_cpu_temp}
            await websocket.send(json.dumps(result_data))
        
    except websockets.exceptions.ConnectionClosed:
        print(f"Client disconnected from {websocket.remote_address}")
    finally:
        # 연결이 끊어졌을 때 처리할 코드 (예: 팬을 안전한 상태로 설정)
        print("Connection closed.")


async def main():
    # 외부 접속을 허용하려면 "0.0.0.0" 사용
    async with websockets.serve(handle_connection, "localhost", 8765):
        print("WebSocket server started at ws://localhost:8765")
        await asyncio.Future()  # 서버가 종료되지 않도록 대기

if __name__ == "__main__":
    asyncio.run(main())